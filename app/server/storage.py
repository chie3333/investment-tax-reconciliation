"""Local-only SQLite snapshot storage with transactional, append-only change audit."""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get('INVESTMENT_DB_PATH', REPOSITORY_ROOT / 'data' / 'investment.sqlite3'))
LOCK = threading.RLock()
COLLECTIONS = ('master', 'assets', 'trades', 'valuations', 'adjustments', 'taxPolicyYears')


class ManagedConnection(sqlite3.Connection):
    """Commit or roll back on context exit, then release the file handle."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10, factory=ManagedConnection)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=10000')
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('CREATE TABLE IF NOT EXISTS workspace (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL)')
    conn.execute('''CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at TEXT NOT NULL, revision INTEGER NOT NULL,
        entity TEXT NOT NULL, entity_key TEXT NOT NULL, action TEXT NOT NULL,
        before_json TEXT, after_json TEXT, actor TEXT NOT NULL DEFAULT 'local-user')''')
    conn.commit()
    return conn


def read_state():
    with LOCK, connect() as conn:
        row = conn.execute('SELECT revision,payload,updated_at FROM workspace WHERE id=1').fetchone()
        return {'revision': row['revision'], 'state': json.loads(row['payload']), 'updated_at': row['updated_at']} if row else {'revision': 0, 'state': None, 'updated_at': None}


def _records(state):
    out = {}
    for name in COLLECTIONS:
        for item in state.get(name, []):
            if not isinstance(item, dict):
                continue
            key = item.get('id', item.get('code', item.get('startYear', item.get('effectiveYear'))))
            if key is None:
                key = json.dumps(item, sort_keys=True, ensure_ascii=False)
            out[(name, str(key))] = item
    for name in ('taxBasis', 'stockPolicies'):
        for key, value in (state.get(name) or {}).items():
            out[(name, str(key))] = value
    for name in ('taxPolicy', 'fiscalEndMonth', 'ids'):
        out[('settings', name)] = state.get(name)
    return out


def save_state(state, expected_revision):
    if not isinstance(state, dict) or not isinstance(expected_revision, int):
        raise ValueError('잘못된 저장 요청입니다.')
    if len(json.dumps(state, ensure_ascii=False)) > 8_000_000:
        raise ValueError('저장 데이터 크기 제한(8MB)을 초과했습니다.')
    for name in COLLECTIONS:
        if not isinstance(state.get(name), list):
            raise ValueError(f'{name}은 배열이어야 합니다.')
    now = datetime.now(timezone.utc).isoformat()
    with LOCK, connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT revision,payload FROM workspace WHERE id=1').fetchone()
        revision = row['revision'] if row else 0
        if revision != expected_revision:
            raise RuntimeError(f'저장 충돌: 서버 버전 {revision}, 요청 버전 {expected_revision}. 새로고침 후 확인하세요.')
        old = json.loads(row['payload']) if row else {}
        from .closing import guard_state_changes
        guard_state_changes(old, state, conn)
        before, after = _records(old), _records(state)
        next_rev = revision + 1
        events = []
        for key in sorted(before.keys() | after.keys()):
            a, b = before.get(key), after.get(key)
            if a == b:
                continue
            action = 'CREATE' if key not in before else 'DELETE' if key not in after else 'UPDATE'
            events.append((now, next_rev, key[0], key[1], action,
                           json.dumps(a, ensure_ascii=False, sort_keys=True) if key in before else None,
                           json.dumps(b, ensure_ascii=False, sort_keys=True) if key in after else None))
        conn.execute('INSERT INTO workspace(id,revision,payload,updated_at) VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,updated_at=excluded.updated_at',
                     (next_rev, json.dumps(state, ensure_ascii=False), now))
        conn.executemany('INSERT INTO audit_log(recorded_at,revision,entity,entity_key,action,before_json,after_json) VALUES(?,?,?,?,?,?,?)', events)
        conn.commit()
        return {'revision': next_rev, 'audit_events': len(events)}


def read_audit(limit=100):
    with LOCK, connect() as conn:
        rows = conn.execute('SELECT * FROM audit_log ORDER BY id DESC LIMIT ?', (min(max(int(limit), 1), 500),)).fetchall()
        return [dict(r) for r in rows]


def save_validation(result, source_revision):
    with LOCK, connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS validation_runs
            (id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at TEXT NOT NULL,
             source_revision INTEGER NOT NULL, fiscal_year INTEGER NOT NULL, result_json TEXT NOT NULL)''')
        cursor=conn.execute('INSERT INTO validation_runs(recorded_at,source_revision,fiscal_year,result_json) VALUES(?,?,?,?)',
             (datetime.now(timezone.utc).isoformat(),source_revision,result['year'],json.dumps(result,ensure_ascii=False)))
        conn.commit()
        return cursor.lastrowid


def read_validation_runs(limit=30):
    with LOCK, connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS validation_runs
            (id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at TEXT NOT NULL,
             source_revision INTEGER NOT NULL, fiscal_year INTEGER NOT NULL, result_json TEXT NOT NULL)''')
        return [dict(row) for row in conn.execute('SELECT id,recorded_at,source_revision,fiscal_year,result_json FROM validation_runs ORDER BY id DESC LIMIT ?', (limit,))]


# Separate review status from immutable historical validation results.
def _review_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS issue_reviews (
      issue_key TEXT PRIMARY KEY, status TEXT NOT NULL CHECK(status IN ('OPEN','REVIEWING','RESOLVED','ACCEPTED')),
      reason TEXT NOT NULL, updated_at TEXT NOT NULL, source_run INTEGER NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS issue_review_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT, issue_key TEXT NOT NULL,
      status TEXT NOT NULL, reason TEXT NOT NULL, recorded_at TEXT NOT NULL, source_run INTEGER NOT NULL)""")
    conn.commit()

def save_issue_review(issue_key, status, reason, run_id):
    if status not in ('OPEN','REVIEWING','RESOLVED','ACCEPTED'):raise ValueError('상태 오류')
    if not issue_key or len(issue_key)>200 or not reason or len(reason)>2000:raise ValueError('대상과 처리 사유가 필요합니다.')
    with LOCK,connect() as conn:
        _review_tables(conn)
        row=conn.execute('SELECT result_json FROM validation_runs WHERE id=?',(run_id,)).fetchone()
        if not row:raise ValueError('검증 실행 이력이 없습니다.')
        result=json.loads(row['result_json'])
        if not any(f"{i['code']}|{i.get('ref','')}"==issue_key for i in result['issues']):raise ValueError('검증 실행 결과에 없는 오류입니다.')
        now=datetime.now(timezone.utc).isoformat()
        conn.execute('INSERT INTO issue_reviews VALUES(?,?,?,?,?) ON CONFLICT(issue_key) DO UPDATE SET status=excluded.status,reason=excluded.reason,updated_at=excluded.updated_at,source_run=excluded.source_run',(issue_key,status,reason,now,run_id))
        conn.execute('INSERT INTO issue_review_events(issue_key,status,reason,recorded_at,source_run) VALUES(?,?,?,?,?)',(issue_key,status,reason,now,run_id))
        conn.commit()
        return {'issue_key':issue_key,'status':status}

def read_issue_reviews():
    with LOCK,connect() as conn:
        _review_tables(conn)
        return {'reviews':[dict(r) for r in conn.execute('SELECT * FROM issue_reviews ORDER BY updated_at DESC')], 'events':[dict(r) for r in conn.execute('SELECT * FROM issue_review_events ORDER BY id DESC LIMIT 200')]}
