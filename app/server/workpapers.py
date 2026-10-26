"""Review workpapers: untrusted computation candidates, human approval, atomic reserve posting."""
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from .storage import connect, LOCK, read_state
from .closing import setup as setup_closing, _amount
from .validation_engine import fiscal_year
from .calculation import calculate_year, candidates_for_year, compare_candidate, evidence, number
from .adjustment_rules import build_plan, verify_adjustments


def setup(c):
    setup_closing(c)
    c.execute('''CREATE TABLE IF NOT EXISTS tax_workpapers (
      id INTEGER PRIMARY KEY AUTOINCREMENT, fiscal_year INTEGER NOT NULL,
      source_revision INTEGER NOT NULL, source_key TEXT NOT NULL,
      candidate_json TEXT NOT NULL, checks_json TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'DRAFT', reviewer TEXT, review_note TEXT,
      approved_json TEXT, created_at TEXT NOT NULL, approved_at TEXT,
      UNIQUE(fiscal_year,source_revision,source_key))''')
    c.execute('''CREATE TABLE IF NOT EXISTS tax_workpaper_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT, workpaper_id INTEGER NOT NULL,
      event TEXT NOT NULL, detail_json TEXT NOT NULL, recorded_at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tax_workpaper_corrections (
      id INTEGER PRIMARY KEY AUTOINCREMENT, workpaper_id INTEGER NOT NULL,
      event TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL,
      recorded_at TEXT NOT NULL, prior_approval_json TEXT NOT NULL)''')
    c.execute('''CREATE UNIQUE INDEX IF NOT EXISTS reserve_workpaper_once
      ON reserve_entries(source_ref) WHERE source_ref LIKE 'WORKPAPER:%' ''')


def _decimal(value):
    try:
        x=Decimal(str(value))
        if not x.is_finite() or abs(x)>Decimal('1000000000000000'): raise ValueError()
        return x
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('금액 형식 또는 범위가 올바르지 않습니다.')


def _checks(state, year, item):
    assets={str(a.get('id')):a for a in state.get('assets',[])}
    aid=str(item.get('asset_id',''))
    source_type=str(item.get('source_type',''))
    sid=str(item.get('source_id',''))
    record_type={'trade':'trades','valuation':'valuations'}.get(source_type)
    record=next((r for r in state.get(record_type,[]) if str(r.get('id'))==sid and str(r.get('assetId'))==aid),None) if record_type else None
    date=record.get('date') if record else ''
    end_month=int(state.get('fiscalEndMonth') or 12)
    return [
      {'name':'등록 자산 확인','passed':aid in assets},
      {'name':'원천 거래·평가 연결','passed':bool(record)},
      {'name':'사업연도 귀속 확인','passed':bool(record) and fiscal_year(date,end_month)==year},
      {'name':'회계분류 일치','passed':bool(record) and assets[aid].get('classification')==item.get('classification')},
      {'name':'계산 엔진 오류 여부','passed':not bool(item.get('error'))},
      {'name':'법령 적용 및 소득처분 검토','passed':False,'manual':True,'note':'자동으로 법적 적정성을 확정할 수 없습니다.'},
      {'name':'원가배분·유보 환입 검토','passed':False,'manual':True,'note':'원가 풀과 기존 유보원장 대사는 담당자가 확인해야 합니다.'},
    ]


def generate(year, revision, candidates=None):
    year=int(year)
    if not 1900<=year<=2200 or (candidates is not None and (not isinstance(candidates,list) or len(candidates)>2000)):raise ValueError('사업연도 또는 후보 건수 오류')
    now=datetime.now(timezone.utc).isoformat()
    with LOCK,connect() as c:
        setup(c);c.execute('BEGIN IMMEDIATE')
        current=read_state(c)
        if revision!=current['revision']:raise ValueError('저장 버전이 달라 검증표를 생성할 수 없습니다.')
        state=current['state'] or {}
        calculated=calculate_year(state,year)
        canonical=candidates_for_year(calculated,year,int(state.get('fiscalEndMonth') or 12))
        for item in canonical:
            item['adjustment_plan']=build_plan(state,year,item)
        if candidates is not None:
            expected={(x['source_type'],str(x['source_id']),str(x['asset_id'])):x for x in canonical}
            seen=set()
            for item in candidates:
                if not isinstance(item,dict):raise ValueError('후보 데이터 형식 오류')
                key=(item.get('source_type'),str(item.get('source_id')),str(item.get('asset_id')))
                if key not in expected or key in seen:raise ValueError('서버 재계산 불일치: 검증표 원천 누락·중복·오류')
                compare_candidate(item,expected[key]);seen.add(key)
            if set(expected)!=seen:raise ValueError('서버 재계산 불일치: 검증표 후보 누락')
        if c.execute('SELECT 1 FROM fiscal_closes WHERE fiscal_year=?',(year,)).fetchone():raise ValueError('확정 사업연도에는 검증표를 생성할 수 없습니다.')
        # Retain obsolete drafts as history; they must not block a fresh review cycle.
        for old in c.execute("SELECT id,source_revision,candidate_json FROM tax_workpapers WHERE fiscal_year=? AND status='DRAFT'",(year,)).fetchall():
            saved=json.loads(old['candidate_json'])
            fresh=next((x for x in canonical if (x['source_type'],x['source_id'],x['asset_id']) ==
                        (saved.get('source_type'),saved.get('source_id'),saved.get('asset_id'))),None)
            if (old['source_revision']!=revision or saved.get('calculation')!=evidence(calculated,revision)
                    or fresh is None or saved.get('adjustment_plan')!=fresh['adjustment_plan']):
                c.execute("UPDATE tax_workpapers SET status='SUPERSEDED' WHERE id=?",(old['id'],))
                c.execute('INSERT INTO tax_workpaper_events(workpaper_id,event,detail_json,recorded_at) VALUES(?,?,?,?)',(old['id'],'SUPERSEDED','{}',now))
        count=0
        for item in canonical:
            item['calculation']=evidence(calculated,revision)
            if not isinstance(item,dict):raise ValueError('후보 데이터 형식 오류')
            source_type=str(item.get('source_type','')); source_id=str(item.get('source_id',''));aid=str(item.get('asset_id',''))
            if source_type not in ('trade','valuation') or not source_id.isdigit() or not aid.isdigit():raise ValueError('원천 식별자 오류')
            for k in ('book_pl','tax_pl','oci_change','candidate_adjustment','reserve_change','oci_equity_transfer'):_decimal(item.get(k,0))
            key=f'{source_type}:{source_id}:{aid}'
            checks=_checks(state,year,item)
            checks.append({'name':'서버 독립 재계산','passed':True,'note':calculated['engine_version']})
            if not all(x['passed'] for x in checks if not x.get('manual')):raise ValueError(f'{key} 원천 데이터 검증 실패')
            active=c.execute("SELECT id FROM tax_workpapers WHERE fiscal_year=? AND source_revision=? AND (source_key=? OR source_key LIKE ?) AND status IN ('DRAFT','APPROVED')",(year,revision,key,key+':R%')).fetchone()
            if active:continue
            existing=c.execute('SELECT id,status FROM tax_workpapers WHERE fiscal_year=? AND source_revision=? AND source_key=?',(year,revision,key)).fetchone()
            if existing:
                if existing['status'] not in ('CANCELLED','SUPERSEDED'):continue
                # Preserve the cancelled record; create a new review cycle.
                prefix=key+':R'
                n=2
                while c.execute('SELECT 1 FROM tax_workpapers WHERE fiscal_year=? AND source_revision=? AND source_key=?',(year,revision,prefix+str(n))).fetchone():n+=1
                key=prefix+str(n)
            c.execute('INSERT INTO tax_workpapers(fiscal_year,source_revision,source_key,candidate_json,checks_json,created_at) VALUES(?,?,?,?,?,?)',
                      (year,revision,key,json.dumps(item,ensure_ascii=False),json.dumps(checks,ensure_ascii=False),now))
            count+=1
        c.commit()
    return {'created':count,'year':year,'source_revision':revision,'calculation':evidence(calculated,revision)}


def verify_candidate(item, calculated, revision, year, month, state=None):
    if item.get('calculation')!=evidence(calculated,revision):
        raise ValueError('서버 재계산 근거가 없거나 변경된 검증표입니다. 기존 승인을 취소하고 재생성하세요.')
    expected=next((row for row in candidates_for_year(calculated,year,month)
                   if row['source_type']==item.get('source_type') and row['source_id']==item.get('source_id') and row['asset_id']==item.get('asset_id')),None)
    if expected is None:raise ValueError('서버 재계산 원천 검증표 오류')
    compare_candidate(item,expected)
    if item.get('allocation_effects')!=expected['allocation_effects'] or item.get('fvoci_disposal_policy')!=expected['fvoci_disposal_policy']:
        raise ValueError('서버 재계산 불일치: 원가 배분 또는 OCI 정책')
    expected['adjustment_plan']=build_plan(state or {},year,expected)
    if item.get('adjustment_plan')!=expected['adjustment_plan']:
        raise ValueError('세무조정 규칙 근거가 없거나 변경되었습니다. 기존 승인 취소 후 재생성하세요.')
    return expected


def verify_reserves(adjustments, expected):
    targets={str(e['assetId']):number(e['reserveChange']) for e in expected['allocation_effects']}
    actual={key:Decimal(0) for key in targets}
    for row in adjustments:
        aid=str(row.get('asset_id',expected['asset_id']))
        movement=number(row.get('reserve_movement',0))
        if aid not in targets:raise ValueError('서버 재계산에 없는 유보 대상 자산입니다.')
        actual[aid]+=movement
    if actual!=targets:raise ValueError('서버 재계산과 승인 유보 증감액이 다릅니다. 자산별 배분효과를 확인하세요.')


def list_workpapers(year):
    with LOCK,connect() as c:
        setup(c)
        rows=[dict(r) for r in c.execute('SELECT * FROM tax_workpapers WHERE fiscal_year=? ORDER BY id',(int(year),))]
        for r in rows:
            for k in ('candidate_json','checks_json','approved_json'):
                if r[k] is not None:r[k[:-5]]=json.loads(r.pop(k))
                else:r.pop(k)
        return {'items':rows}


def approve(workpaper_id,reviewer,note,adjustments,acknowledge):
    if not str(reviewer).strip() or not str(note).strip() or len(str(note))<10:raise ValueError('검토자와 구체적인 검토 의견(10자 이상)이 필요합니다.')
    if acknowledge is not True:raise ValueError('법령 적용 및 계산 검토 확인이 필요합니다.')
    if not isinstance(adjustments,list) or len(adjustments)>20:raise ValueError('세무조정 행 형식 오류')
    prepared=[]
    allowed_directions={'익금산입','손금산입','익금불산입','손금불산입'}
    allowed_dispositions={'유보','△유보','기타','기타사외유출','상여','배당'}
    for x in adjustments:
        direction=x.get('direction'); disposition=x.get('disposition');amount=_amount(x.get('amount'))
        if direction not in allowed_directions or disposition not in allowed_dispositions or amount<=0:raise ValueError('조정 방향·소득처분·금액 오류')
        signed=_amount(x.get('reserve_movement',0))
        if disposition=='유보' and signed<=0:raise ValueError('유보는 양수 증감액이 필요합니다.')
        if disposition=='△유보' and signed>=0:raise ValueError('△유보는 음수 증감액이 필요합니다.')
        if disposition not in ('유보','△유보') and signed!=0:raise ValueError('유보 외 처분은 유보원장에 반영할 수 없습니다.')
        if disposition in ('유보','△유보') and abs(signed)!=amount:raise ValueError('유보 증감액과 조정금액이 일치해야 합니다.')
        row={'direction':direction,'disposition':disposition,'amount':str(amount),'reserve_movement':str(signed),'legal_basis':str(x.get('legal_basis','')).strip()}
        if x.get('asset_id') is not None:row['asset_id']=str(x['asset_id'])
        prepared.append(row)
    if any(not a['legal_basis'] for a in prepared):raise ValueError('각 조정 행의 법령·검토 근거를 입력하세요.')
    now=datetime.now(timezone.utc).isoformat()
    with LOCK,connect() as c:
        setup(c);c.execute('BEGIN IMMEDIATE')
        r=c.execute('SELECT * FROM tax_workpapers WHERE id=?',(int(workpaper_id),)).fetchone()
        if not r:raise ValueError('검증표를 찾을 수 없습니다.')
        if r['status']!='DRAFT':raise ValueError('승인 가능한 초안이 아닙니다.')
        if c.execute('SELECT 1 FROM fiscal_closes WHERE fiscal_year=?',(r['fiscal_year'],)).fetchone():raise ValueError('확정된 사업연도입니다.')
        current=read_state(c)
        if current['revision']!=r['source_revision']:raise ValueError('원본 데이터가 변경되었습니다. 검증표를 다시 생성하세요.')
        item=json.loads(r['candidate_json']);checks=_checks(current['state'] or {},r['fiscal_year'],item)
        if not all(x['passed'] for x in checks if not x.get('manual')):raise ValueError('원천 검증을 통과하지 못했습니다.')
        calculated=calculate_year(current['state'] or {},r['fiscal_year'])
        expected=verify_candidate(item,calculated,current['revision'],r['fiscal_year'],int((current['state'] or {}).get('fiscalEndMonth') or 12),current['state'])
        verify_reserves(prepared,expected)
        verify_adjustments(prepared,expected,expected['adjustment_plan'])
        # A FVOCI disposal must explicitly document its reserve release, rather than
        # silently treating tax profit or OCI equity transfer as a reserve reversal.
        if not prepared and any(_decimal(item.get(k,0))!=0 for k in ('candidate_adjustment','reserve_change','oci_change')):
            raise ValueError('조정 후보가 존재하므로 세무조정 행 없이 승인할 수 없습니다.')
        if item.get('classification')=='FVOCI' and item.get('event')=='SELL':
            if _decimal(item.get('reserve_change',0))!=0 and not any(a['disposition'] in ('유보','△유보') for a in prepared):
                raise ValueError('FVOCI 처분 유보 환입 후보가 존재하므로 유보 조정 행과 잔액 대사가 필요합니다.')
        for i,a in enumerate(prepared):
            if a['disposition'] not in ('유보','△유보'):continue
            source=f'WORKPAPER:{r["id"]}:{i}'
            target_id=str(a.get('asset_id',item['asset_id']))
            target=next(x for x in current['state']['assets'] if str(x['id'])==target_id)
            c.execute('INSERT INTO reserve_entries(fiscal_year,asset_id,adjustment_key,source_ref,amount,memo,recorded_at) VALUES(?,?,?,?,?,?,?)',
                      (r['fiscal_year'],target_id,f'{target["classification"]}_{item["source_type"].upper()}',source,a['reserve_movement'],str(note),now))
        approval={'adjustments':prepared,'acknowledged':True,'calculation':evidence(calculated,current['revision']),
                  'adjustment_plan':expected['adjustment_plan']}
        c.execute('UPDATE tax_workpapers SET status=?,reviewer=?,review_note=?,approved_json=?,approved_at=? WHERE id=?',
                  ('APPROVED',str(reviewer),str(note),json.dumps(approval,ensure_ascii=False),now,r['id']))
        c.execute('INSERT INTO tax_workpaper_events(workpaper_id,event,detail_json,recorded_at) VALUES(?,?,?,?)',
                  (r['id'],'APPROVED',json.dumps({'reviewer':reviewer,'adjustments':prepared},ensure_ascii=False),now))
        c.commit()
    return {'id':int(workpaper_id),'status':'APPROVED','reserve_posted':sum(a['disposition'] in ('유보','△유보') for a in prepared)}


def cancel_approval(workpaper_id, actor, reason):
    """Append compensating reserve movements; never delete approved evidence."""
    if not str(actor).strip() or len(str(reason).strip())<10:
        raise ValueError('정정 담당자와 정정 사유(10자 이상)가 필요합니다.')
    now=datetime.now(timezone.utc).isoformat()
    with LOCK,connect() as c:
        setup(c);c.execute('BEGIN IMMEDIATE')
        r=c.execute('SELECT * FROM tax_workpapers WHERE id=?',(int(workpaper_id),)).fetchone()
        if not r or r['status']!='APPROVED':raise ValueError('취소 가능한 승인 건이 아닙니다.')
        if c.execute('SELECT 1 FROM fiscal_closes WHERE fiscal_year=?',(r['fiscal_year'],)).fetchone():
            raise ValueError('결산 확정 이후에는 취소할 수 없습니다. 별도 재개방 절차가 필요합니다.')
        old=json.loads(r['approved_json']);candidate=json.loads(r['candidate_json'])
        original=list(c.execute('SELECT * FROM reserve_entries WHERE source_ref LIKE ? ORDER BY id',(f'WORKPAPER:{r["id"]}:%',)))
        for entry in original:
            ref=f'REVERSAL:{r["id"]}:{entry["id"]}'
            if c.execute('SELECT 1 FROM reserve_entries WHERE source_ref=?',(ref,)).fetchone():raise ValueError('이미 취소 전기가 존재합니다.')
            c.execute('INSERT INTO reserve_entries(fiscal_year,asset_id,adjustment_key,source_ref,amount,memo,recorded_at) VALUES(?,?,?,?,?,?,?)',
              (r['fiscal_year'],entry['asset_id'],entry['adjustment_key'],ref,str(-_amount(entry['amount'])),f'승인 취소 #{r["id"]}: {reason}',now))
        c.execute('UPDATE tax_workpapers SET status=? WHERE id=?',('CANCELLED',r['id']))
        c.execute('INSERT INTO tax_workpaper_corrections(workpaper_id,event,actor,reason,recorded_at,prior_approval_json) VALUES(?,?,?,?,?,?)',
          (r['id'],'CANCEL',str(actor),str(reason),now,r['approved_json']))
        c.execute('INSERT INTO tax_workpaper_events(workpaper_id,event,detail_json,recorded_at) VALUES(?,?,?,?)',
          (r['id'],'CANCELLED',json.dumps({'actor':actor,'reason':reason,'reversed_entries':len(original)},ensure_ascii=False),now))
        c.commit()
    return {'id':int(workpaper_id),'status':'CANCELLED','reversed_entries':len(original)}
