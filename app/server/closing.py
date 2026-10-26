"""Conservative year-end close and manually reviewed reserve subledger.

Amounts are signed: positive=유보, negative=△유보. A close is not a tax filing.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from .storage import connect, LOCK, read_state
from .validation_engine import validate, fiscal_year


def _amount(v):
    try:
        n=Decimal(str(v))
        if not n.is_finite() or n.as_tuple().exponent < -2: raise ValueError('금액은 소수 둘째 자리까지 입력하세요.')
        return n
    except (InvalidOperation, TypeError):raise ValueError('유효한 금액이 아닙니다.')


def setup(c):
    c.execute('''CREATE TABLE IF NOT EXISTS reserve_entries (
      id INTEGER PRIMARY KEY AUTOINCREMENT, fiscal_year INTEGER NOT NULL,
      asset_id TEXT NOT NULL, adjustment_key TEXT NOT NULL, source_ref TEXT NOT NULL,
      amount TEXT NOT NULL, memo TEXT NOT NULL, recorded_at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS fiscal_closes (
      fiscal_year INTEGER PRIMARY KEY, source_revision INTEGER NOT NULL,
      validation_run INTEGER NOT NULL, closed_at TEXT NOT NULL,
      opening_json TEXT NOT NULL, movements_json TEXT NOT NULL, ending_json TEXT NOT NULL,
      reviewer TEXT NOT NULL, note TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS closing_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT, fiscal_year INTEGER NOT NULL,
      event TEXT NOT NULL, recorded_at TEXT NOT NULL, detail_json TEXT NOT NULL)''')


def _balances(rows):
    result={}
    for r in rows:
        k=f"{r['asset_id']}|{r['adjustment_key']}"
        result[k]=result.get(k,Decimal(0))+_amount(r['amount'])
    return {k:str(v) for k,v in sorted(result.items()) if v}


def _merged(opening,movements):
    out={k:_amount(v) for k,v in opening.items()}
    for k,v in movements.items():out[k]=out.get(k,Decimal(0))+_amount(v)
    return {k:str(v) for k,v in sorted(out.items()) if v}


def add_entry(year, asset_id, adjustment_key, source_ref, amount, memo):
    year=int(year); amount=_amount(amount)
    if year<1900 or year>2200:raise ValueError('사업연도 범위 오류')
    for val in (asset_id,adjustment_key,source_ref,memo):
        if not str(val).strip() or len(str(val))>500:raise ValueError('유보 항목·원천·사유는 필수입니다.')
    raise ValueError('수동 유보원장 입력은 비활성화되었습니다. 검증표 승인 또는 승인 취소를 통해 전기하세요.')
    with LOCK,connect() as c:
        setup(c); c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM fiscal_closes WHERE fiscal_year=?',(year,)).fetchone():raise ValueError('확정된 사업연도에는 유보를 추가할 수 없습니다.')
        st=read_state()['state'] or {}
        if str(asset_id) not in {str(a.get('id')) for a in st.get('assets',[])}:raise ValueError('등록된 투자자산이 아닙니다.')
        now=datetime.now(timezone.utc).isoformat()
        cur=c.execute('INSERT INTO reserve_entries(fiscal_year,asset_id,adjustment_key,source_ref,amount,memo,recorded_at) VALUES(?,?,?,?,?,?,?)',(year,str(asset_id),str(adjustment_key),str(source_ref),str(amount),str(memo),now))
        c.execute('INSERT INTO closing_events(fiscal_year,event,recorded_at,detail_json) VALUES(?,?,?,?)',(year,'RESERVE_ENTRY',now,json.dumps({'id':cur.lastrowid,'amount':str(amount),'asset_id':str(asset_id),'adjustment_key':str(adjustment_key)},ensure_ascii=False)))
        c.commit();return {'entry_id':cur.lastrowid}


def summary(year):
    year=int(year)
    with LOCK,connect() as c:
        setup(c)
        prev=c.execute('SELECT * FROM fiscal_closes WHERE fiscal_year=?',(year-1,)).fetchone()
        opening=json.loads(prev['ending_json']) if prev else {}
        rows=[dict(r) for r in c.execute('SELECT * FROM reserve_entries WHERE fiscal_year=? ORDER BY id',(year,))]
        movements=_balances(rows)
        closed=c.execute('SELECT * FROM fiscal_closes WHERE fiscal_year=?',(year,)).fetchone()
        return {'year':year,'prior_closed':bool(prev),'opening':opening,'movements':movements,'ending':_merged(opening,movements),'entries':rows,'close':dict(closed) if closed else None}


def close_year(year, reviewer, note, acknowledge_warnings=False, positions=None):
    year=int(year)
    if not str(reviewer).strip() or not str(note).strip():raise ValueError('확정자와 검토 메모가 필요합니다.')
    with LOCK,connect() as c:
        setup(c);c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM fiscal_closes WHERE fiscal_year=?',(year,)).fetchone():raise ValueError('이미 확정된 사업연도입니다.')
        current=read_state();st=current['state'] or {}
        # A closing must use a previously saved validation run on exactly the same source revision.
        row=c.execute('SELECT id,result_json FROM validation_runs WHERE fiscal_year=? AND source_revision=? ORDER BY id DESC LIMIT 1',(year,current['revision'])).fetchone()
        if not row:raise ValueError('현재 데이터 버전으로 자동 검증을 먼저 실행하세요.')
        result=json.loads(row['result_json'])
        if result['summary']['ERROR']:raise ValueError('검증 오류가 남아 있어 결산 확정이 차단됩니다.')
        if result['summary']['WARNING'] and not acknowledge_warnings:raise ValueError('검증 경고를 검토하고 승인 여부를 명시하세요.')
        from .workpapers import setup as setup_workpapers
        setup_workpapers(c)
        candidates=c.execute('SELECT id,status,source_key,source_revision FROM tax_workpapers WHERE fiscal_year=?',(year,)).fetchall()
        if any(r['status']=='DRAFT' for r in candidates):
            raise ValueError('미승인 검증표가 있어 결산 확정이 차단됩니다.')
        if any(r['source_revision']!=current['revision'] for r in candidates if r['status']=='APPROVED'):
            raise ValueError('원본 데이터가 변경된 검증표가 있습니다. 재검증이 필요합니다.')
        # Coverage: every SELL and VALUATION source must have an approved workpaper.
        month=int(st.get('fiscalEndMonth') or 12)
        required=set()
        for table,kind in (('trades','trade'),('valuations','valuation')):
            for r in st.get(table,[]):
                if (table=='valuations' or r.get('type')=='SELL') and fiscal_year(r['date'],month)==year:
                    required.add(f'{kind}:{r["id"]}:{r["assetId"]}')
        approved={r['source_key'].split(':R')[0] for r in candidates if r['status']=='APPROVED'}
        if required-approved:raise ValueError('미작성 검증표: '+', '.join(sorted(required-approved)[:5]))
        if not isinstance(positions,list):raise ValueError('기말 회계·세무 장부금액 대사 자료가 필요합니다.')
        assets={str(a['id']):a for a in st.get('assets',[])}
        actual={}
        for item in positions:
            aid=str(item.get('assetId'))
            if aid not in assets or aid in actual:raise ValueError('기말 대사 자산 ID 오류')
            qty=_amount(item.get('qty')); book=_amount(item.get('book'));tax=_amount(item.get('tax'))
            actual[aid]={'qty':qty,'book':book,'tax':tax}
        if set(actual)!=set(assets):raise ValueError('모든 투자자산의 기말 대사 자료가 필요합니다.')
        for aid,asset in assets.items():
            qty=sum((_amount(t.get('qty',0))*(1 if t.get('type')=='BUY' else -1)
                     for t in st.get('trades',[]) if str(t.get('assetId'))==aid and t.get('type') in ('BUY','SELL')
                     and t.get('date','')<=_fiscal_end_date(year,month)),Decimal(0))
            if qty!=actual[aid]['qty']:raise ValueError(f'{aid} 기말 수량 대사 불일치')
        prev=c.execute('SELECT ending_json FROM fiscal_closes WHERE fiscal_year=?',(year-1,)).fetchone()
        if not prev and any(c.execute('SELECT 1 FROM reserve_entries WHERE fiscal_year<? LIMIT 1',(year,)).fetchone() for _ in (0,)):
            raise ValueError('이전 사업연도 유보 항목이 있지만 직전 결산이 확정되지 않았습니다.')
        opening=json.loads(prev['ending_json']) if prev else {}
        legacy=c.execute("SELECT COUNT(*) FROM reserve_entries WHERE fiscal_year=? AND source_ref NOT LIKE 'WORKPAPER:%' AND source_ref NOT LIKE 'REVERSAL:%'",(year,)).fetchone()[0]
        if legacy:raise ValueError('기존 수동 유보원장 항목이 남아 있습니다. 검증표 승인으로 이관하고 대사한 후 확정하세요.')
        movements=_balances([dict(r) for r in c.execute('SELECT * FROM reserve_entries WHERE fiscal_year=?',(year,))])
        ending=_merged(opening,movements)
        # Signed reserve must equal tax basis minus book basis by asset. If an
        # unexplained other temporary difference exists, close is deliberately blocked.
        for aid,v in actual.items():
            balance=sum((_amount(amount) for key,amount in ending.items() if key.split('|')[0]==aid),Decimal(0))
            expected=v['tax']-v['book']
            if abs(balance-expected)>Decimal('1'):
                raise ValueError(f'자산 {aid} 유보 대사 불일치: 원장 {balance}, 세무-회계 {expected}. FVOCI 처분 환입·원가 배분을 재검토하세요.')
        # Classification-level tax basis must add up to stock-wide tax basis;
        # this is a second, distinct reconciliation from reserve balances.
        for code in {a.get('stock_code') for a in assets.values()}:
            matching=[aid for aid,a in assets.items() if a.get('stock_code')==code]
            if any(actual[aid]['qty']==0 and (abs(actual[aid]['book'])>1 or abs(actual[aid]['tax'])>1) for aid in matching):
                raise ValueError(f'{code} 전량 처분 자산 장부 잔액이 남았습니다.')
        now=datetime.now(timezone.utc).isoformat()
        c.execute('INSERT INTO fiscal_closes VALUES(?,?,?,?,?,?,?,?,?)',(year,current['revision'],row['id'],now,json.dumps(opening),json.dumps(movements),json.dumps(ending),str(reviewer),str(note)))
        c.execute('INSERT INTO closing_events(fiscal_year,event,recorded_at,detail_json) VALUES(?,?,?,?)',(year,'CLOSE',now,json.dumps({'revision':current['revision'],'validation_run':row['id'],'reviewer':reviewer,'warnings_approved':bool(acknowledge_warnings)},ensure_ascii=False)))
        c.commit()
    return summary(year)


def guard_state_changes(before,after,connection=None):
    """Reject editing historical economic records once their fiscal year is closed."""
    if connection is None:
        with connect() as c:
            setup(c)
            closed=[r[0] for r in c.execute('SELECT fiscal_year FROM fiscal_closes')]
    else:
        closed=[r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='fiscal_closes'")]
        closed=[r[0] for r in connection.execute("SELECT fiscal_year FROM fiscal_closes")] if closed else []
    if not closed:return
    month=int(before.get('fiscalEndMonth') or 12)
    if after.get('fiscalEndMonth',12)!=month:raise ValueError('확정된 결산이 있어 결산월을 변경할 수 없습니다.')
    for name in ('trades','valuations','adjustments'):
        old={str(x.get('id')):x for x in before.get(name,[])}
        new={str(x.get('id')):x for x in after.get(name,[])}
        for k in old.keys()|new.keys():
            a,b=old.get(k),new.get(k)
            if a==b:continue
            for record in (a,b):
                if record and record.get('date'):
                    try: fy=fiscal_year(record['date'],month)
                    except ValueError:raise ValueError('거래 날짜가 잘못되었습니다.')
                    if fy in closed:raise ValueError(f'{fy}사업연도는 결산 확정되어 {name} 변경이 차단됩니다.')
    for name in ('assets','stockPolicies','taxPolicyYears','taxPolicy','taxBasis'):
        if before.get(name)!=after.get(name):raise ValueError(f'확정 결산이 있어 {name} 변경은 별도 재개방 절차가 필요합니다.')


def _fiscal_end_date(year,month):
    from calendar import monthrange
    return f'{year:04d}-{month:02d}-{monthrange(year,month)[1]:02d}'
