"""Local-only SQLite snapshot storage with transactional, append-only change audit."""
import json
import os
import sqlite3
import threading
from contextlib import closing
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
    from app.database import APPLICATION_ID, SCHEMA_VERSION
    if (conn.execute('PRAGMA application_id').fetchone()[0]==APPLICATION_ID or
            conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_metadata'").fetchone()):
        if conn.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
            conn.close()
            raise ValueError('지원하지 않는 정규화 DB 버전입니다. 기존 파일을 보존하고 현재 버전의 새 DB를 지정하세요.')
        from app.database.bootstrap import configure
        configure(conn)
        return conn
    conn.execute('PRAGMA busy_timeout=10000')
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('CREATE TABLE IF NOT EXISTS workspace (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL)')
    conn.execute('''CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at TEXT NOT NULL, revision INTEGER NOT NULL,
        entity TEXT NOT NULL, entity_key TEXT NOT NULL, action TEXT NOT NULL,
        before_json TEXT, after_json TEXT, actor TEXT NOT NULL DEFAULT 'local-user')''')
    conn.commit()
    return conn


def using_normalized():
    if not DB_PATH.exists():return False
    from app.database.repository import is_normalized
    with closing(sqlite3.connect(f'{DB_PATH.resolve().as_uri()}?mode=ro',uri=True)) as c:
        return is_normalized(c)


def read_state(connection=None):
    if connection is not None:
        from app.database import repository
        if repository.is_normalized(connection):return repository.read_state(connection)
        row = connection.execute('SELECT revision,payload,updated_at FROM workspace WHERE id=1').fetchone()
        return {'revision': row['revision'], 'state': json.loads(row['payload']), 'updated_at': row['updated_at']} if row else {'revision': 0, 'state': None, 'updated_at': None}
    with LOCK, connect() as conn:
        return read_state(conn)


def _records(state):
    out = {}
    for name in COLLECTIONS + ('fvociPolicyYears',):
        for item in state.get(name, []):
            if not isinstance(item, dict):
                continue
            key = item.get('effectiveYear') if name=='fvociPolicyYears' else item.get('id', item.get('code', item.get('startYear', item.get('effectiveYear'))))
            if key is None:
                key = json.dumps(item, sort_keys=True, ensure_ascii=False)
            out[(name, str(key))] = item
    for name in ('taxBasis', 'stockPolicies'):
        for key, value in (state.get(name) or {}).items():
            out[(name, str(key))] = value
    for name in ('taxPolicy', 'fiscalEndMonth', 'fvociPolicy', 'taxAdjustmentScope', 'ids', 'roundingPolicy'):
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
    if 'taxAdjustmentScope' in state and not isinstance(state['taxAdjustmentScope'],dict):
        raise ValueError('세무조정 적용범위는 객체여야 합니다.')
    from .fvoci_policy import policy_schedule
    policy_schedule(state)
    from .rounding import policy
    policy(state)
    now = datetime.now(timezone.utc).isoformat()
    with LOCK, connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        from app.database import repository
        if repository.is_normalized(conn):
            try:return repository.save_state(conn,state,expected_revision)
            except sqlite3.IntegrityError as exc:raise ValueError('DB 무결성 검사: '+str(exc)) from exc
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
        from app.database.repository import is_normalized
        if is_normalized(conn):
            return [dict(r) for r in conn.execute('SELECT audit_id AS id,recorded_at,source_revision AS revision,entity_type AS entity,entity_key,action,before_json,after_json,actor FROM audit_log WHERE company_id=1 ORDER BY audit_id DESC LIMIT ?',(min(max(int(limit),1),500),))]
        rows = conn.execute('SELECT * FROM audit_log ORDER BY id DESC LIMIT ?', (min(max(int(limit), 1), 500),)).fetchall()
        return [dict(r) for r in rows]


def save_validation(result, source_revision, connection=None):
    if connection is not None:
        from app.database.repository import is_normalized
        if is_normalized(connection):raise ValueError('정규화 DB 검증은 서버 독립 재계산 서비스를 사용하세요.')
        connection.execute('''CREATE TABLE IF NOT EXISTS validation_runs
            (id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at TEXT NOT NULL,
             source_revision INTEGER NOT NULL, fiscal_year INTEGER NOT NULL, result_json TEXT NOT NULL)''')
        cursor=connection.execute('INSERT INTO validation_runs(recorded_at,source_revision,fiscal_year,result_json) VALUES(?,?,?,?)',
             (datetime.now(timezone.utc).isoformat(),source_revision,result['year'],json.dumps(result,ensure_ascii=False)))
        return cursor.lastrowid
    with LOCK, connect() as conn:
        return save_validation(result, source_revision, conn)


def read_validation_runs(limit=30):
    with LOCK, connect() as conn:
        from app.database.repository import is_normalized
        if is_normalized(conn):
            return [dict(r) for r in conn.execute('SELECT v.validation_id AS id,v.created_at AS recorded_at,v.source_revision,y.fiscal_year,v.result_json FROM validation_runs v JOIN fiscal_years y ON y.fiscal_year_id=v.fiscal_year_id WHERE v.company_id=1 ORDER BY v.validation_id DESC LIMIT ?',(limit,))]
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
    if using_normalized():
        from app.database.services import save_issue_review as normalized
        return normalized(issue_key,status,reason,run_id)
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
    if using_normalized():
        from app.database.services import read_issue_reviews as normalized
        return normalized()
    with LOCK,connect() as conn:
        _review_tables(conn)
        return {'reviews':[dict(r) for r in conn.execute('SELECT * FROM issue_reviews ORDER BY updated_at DESC')], 'events':[dict(r) for r in conn.execute('SELECT * FROM issue_review_events ORDER BY id DESC LIMIT 200')]}
