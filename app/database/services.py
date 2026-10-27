"""Normalized validation/approval/ledger/closing services behind the existing API."""
import functools
import sqlite3
from decimal import Decimal

from . import repository as repo
from app.server.calculation import calculate_year, candidates_for_year, compare_candidate, compare_positions, evidence
from app.server.adjustment_rules import build_plan, verify_adjustments, normalize_adjustment_label
from app.server.validation_engine import validate


def transaction(write=True):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args,**kwargs):
            from app.server import storage
            with storage.LOCK,storage.connect() as c:
                c.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                try:
                    result=function(c,*args,**kwargs)
                    c.commit();return result
                except sqlite3.IntegrityError as exc:
                    raise ValueError('DB 무결성 검사: '+str(exc)) from exc
        return wrapped
    return decorate


def context(c,year,revision=None):
    year=int(year)
    if not 1900<=year<=2200:raise ValueError('사업연도 범위 오류')
    current=repo.read_state(c)
    if current['state'] is None:raise ValueError('기초 자료를 먼저 저장하세요.')
    if revision is not None and revision!=current['revision']:raise ValueError('저장 버전이 달라 작업을 중단했습니다.')
    state=current['state'];calculated=calculate_year(state,year)
    return state,calculated,current['revision']


def store_calculation(c,state,calculated,revision,year):
    from app.server.rounding import profile_for_year,ensure_profile,WON
    profile=profile_for_year(state,year)
    if profile==WON:ensure_profile(c)
    fy=repo.period(c,year,int(state.get('fiscalEndMonth') or 12))
    row=c.execute('SELECT run_id FROM calculation_runs WHERE company_id=? AND fiscal_year_id=? AND source_revision=? AND input_hash=? AND result_hash=?',
                  (repo.COMPANY,fy,revision,repo.digest(state),calculated['digest'])).fetchone()
    if row:return row[0],fy
    run=repo.insert(c,'calculation_runs',company_id=repo.COMPANY,fiscal_year_id=fy,as_of=calculated['as_of'],source_revision=revision,
                    engine_version=calculated['engine_version'],rounding_profile_id=profile,inputs_json=repo.dumps(state),input_hash=repo.digest(state),
                    result_hash=calculated['digest'],status='SUCCEEDED',created_at=repo.now())
    for p in calculated['positions']:
        repo.insert(c,'calculated_balances',company_id=repo.COMPANY,run_id=run,asset_id=p['assetId'],quantity=p['qty'],book_value=p['book'],tax_value=p['tax'],
                    reserve=p['reserve'],oci=p['oci'],active_oci=p['activeOCI'],retained_disposal_oci=p['retainedDisposalOCI'],profit_loss=p['pl'],dividend=p['dividend'])
    for pool in calculated['stockPools']:
        stock_id=c.execute('SELECT stock_id FROM stock_master WHERE stock_code=? AND is_current_code=1',(pool['stock_code'],)).fetchone()[0]
        ids={a['id'] for a in state['assets'] if a['stock_code']==pool['stock_code']}
        repo.insert(c,'calculated_stock_pools',company_id=repo.COMPANY,run_id=run,stock_id=stock_id,
                    quantity=sum(p['qty'] for p in calculated['positions'] if p['assetId'] in ids),tax_value=pool['tax'],method_snapshot=repo.dumps(state.get('taxPolicyYears') or state.get('taxPolicy') or {}))
    return run,fy


def balance_map(rows):
    totals={}
    for row in rows:
        key=f"{row['asset_id']}|{row['adjustment_key']}";totals[key]=totals.get(key,Decimal(0))+Decimal(row['amount'])
    return {k:str(v) for k,v in sorted(totals.items()) if v}


def closing_summary(c,year):
    year=int(year)
    prior=c.execute('SELECT z.* FROM fiscal_closes z JOIN fiscal_years y ON y.fiscal_year_id=z.fiscal_year_id WHERE z.company_id=? AND y.fiscal_year=?',(repo.COMPANY,year-1)).fetchone()
    closed=c.execute('SELECT z.* FROM fiscal_closes z JOIN fiscal_years y ON y.fiscal_year_id=z.fiscal_year_id WHERE z.company_id=? AND y.fiscal_year=?',(repo.COMPANY,year)).fetchone()
    entries=[]
    for row in c.execute('SELECT e.*,r.row_no FROM reserve_entries e JOIN fiscal_years y ON y.fiscal_year_id=e.fiscal_year_id JOIN tax_adjustment_rows r ON r.adjustment_row_id=e.adjustment_row_id WHERE e.company_id=? AND y.fiscal_year=? ORDER BY e.entry_id',(repo.COMPANY,year)):
        entries.append(dict(id=row['entry_id'],fiscal_year=year,asset_id=str(row['asset_id']),adjustment_key=row['adjustment_key'],amount=row['amount'],memo=row['memo'],recorded_at=row['recorded_at'],
                            source_ref=row['legacy_source_ref'] or (f"WORKPAPER:{row['workpaper_id']}:{row['row_no']-1}" if row['entry_type']=='POST' else f"REVERSAL:{row['workpaper_id']}:{row['reversal_of_entry_id']}")))
    opening=repo.loads(prior['ending_json']) if prior else {};movements=balance_map(entries)
    ending={k:str(Decimal(opening.get(k,'0'))+Decimal(movements.get(k,'0'))) for k in opening.keys()|movements.keys() if Decimal(opening.get(k,'0'))+Decimal(movements.get(k,'0'))}
    return dict(year=year,prior_closed=bool(prior),opening=opening,movements=movements,ending=ending,entries=entries,
                close=({**dict(closed),'fiscal_year':year,'validation_run':closed['validation_id']} if closed else None))


def validation_result(c,state,year,calculated,browser=None):
    comparison=dict(calculated);prior=closing_summary(c,year)
    if prior['prior_closed']:
        amounts={}
        for key,value in prior['opening'].items():amounts[key.split('|')[0]]=amounts.get(key.split('|')[0],Decimal(0))+Decimal(value)
        opening=calculate_year(state,year-1)
        comparison['prior_reserves']={str(p['assetId']):str(amounts.get(str(p['assetId']),Decimal(0))) for p in opening['positions']}
        comparison['opening_reserves']={str(p['assetId']):p['reserve'] for p in opening['positions']}
    result=validate(state,year,comparison)
    if browser is not None:
        try:compare_positions(browser.get('positions'),calculated)
        except (ValueError,TypeError,AttributeError) as exc:
            result['issues'].append(dict(code='SERVER_CALC_MISMATCH',severity='ERROR',title='브라우저·서버 재계산 불일치',detail=str(exc),ref=str(year),expected=None,actual=None))
            result['summary']['ERROR']+=1
    return result


@transaction()
def run_validation(c,year,revision,browser=None):
    year=int(year);state,calculated,revision=context(c,year,revision)
    run,fy=store_calculation(c,state,calculated,revision,year)
    result=validation_result(c,state,year,calculated,browser)
    identifier=c.execute('SELECT coalesce(max(validation_id),0)+1 FROM validation_runs').fetchone()[0]
    result.update(calculation=evidence(calculated,revision),positions=calculated['positions'],run_id=identifier)
    repo.insert(c,'validation_runs',validation_id=identifier,company_id=repo.COMPANY,run_id=run,fiscal_year_id=fy,source_revision=revision,evidence_state='VERIFIED',
                summary_json=repo.dumps(result['summary']),issues_json=repo.dumps(result['issues']),created_at=repo.now(),result_json=repo.dumps(result))
    return result


def source_key(item):return f"{item['source_type']}:{item['source_id']}:{item['asset_id']}"


def paper_items(c,year):
    items=[]
    for row in c.execute('SELECT w.*,r.source_revision FROM tax_workpapers w JOIN calculation_runs r ON r.run_id=w.run_id JOIN fiscal_years y ON y.fiscal_year_id=w.fiscal_year_id WHERE w.company_id=? AND y.fiscal_year=? ORDER BY w.workpaper_id',(repo.COMPANY,int(year))):
        candidate=repo.loads(row['candidate_json'])
        approved=c.execute("SELECT * FROM workpaper_decisions WHERE workpaper_id=? AND action='APPROVE'",(row['workpaper_id'],)).fetchone()
        approval=repo.loads(approved['proof_json']) if approved else None
        if approval is not None:approval['adjustments']=repo.loads(approved['approved_rows_json'])
        items.append(dict(id=row['workpaper_id'],fiscal_year=int(year),source_revision=row['source_revision'],
                     source_key=source_key(candidate)+(f":R{row['cycle']}" if row['cycle']>1 else ''),candidate=candidate,checks=repo.loads(row['checks_json']),
                     status=row['status'],reviewer=approved['actor'] if approved else None,review_note=approved['reason'] if approved else None,
                     approved=approval,created_at=row['created_at'],approved_at=approved['created_at'] if approved else None))
    return items


@transaction(False)
def list_workpapers(c,year):return {'items':paper_items(c,int(year))}


@transaction(False)
def summary(c,year):return closing_summary(c,int(year))


@transaction()
def save_issue_review(c,issue_key,status,reason,run_id):
    if status not in ('OPEN','REVIEWING','RESOLVED','ACCEPTED'):raise ValueError('상태 오류')
    if not issue_key or len(issue_key)>200 or not reason or len(reason)>2000:raise ValueError('대상과 처리 사유가 필요합니다.')
    run=c.execute('SELECT * FROM validation_runs WHERE company_id=? AND validation_id=?',(repo.COMPANY,run_id)).fetchone()
    if not run:raise ValueError('검증 실행 이력이 없습니다.')
    issue=next((i for i in repo.loads(run['result_json'])['issues'] if f"{i['code']}|{i.get('ref','')}"==issue_key),None)
    if issue is None:raise ValueError('검증 실행 결과에 없는 오류입니다.')
    old=c.execute('SELECT * FROM issue_reviews WHERE company_id=? AND fiscal_year_id=? AND issue_code=? AND source_ref=?',
                  (repo.COMPANY,run['fiscal_year_id'],issue['code'],str(issue.get('ref','')))).fetchone()
    stamp=repo.now()
    if old:
        identifier=old['issue_id']
        c.execute('UPDATE issue_reviews SET validation_id=?,status=?,reason=?,updated_at=? WHERE issue_id=?',(run_id,status,reason,stamp,identifier))
    else:
        identifier=repo.insert(c,'issue_reviews',company_id=repo.COMPANY,fiscal_year_id=run['fiscal_year_id'],issue_code=issue['code'],
                               source_ref=str(issue.get('ref','')),validation_id=run_id,status=status,reason=reason,updated_at=stamp)
    repo.insert(c,'issue_review_events',company_id=repo.COMPANY,fiscal_year_id=run['fiscal_year_id'],issue_id=identifier,validation_id=run_id,
                previous_status=old['status'] if old else None,status=status,actor='local-user',reason=reason,created_at=stamp)
    return dict(issue_key=issue_key,status=status)


@transaction(False)
def read_issue_reviews(c):
    reviews=[dict(r) for r in c.execute("SELECT issue_code||'|'||source_ref AS issue_key,status,reason,updated_at,validation_id AS source_run FROM issue_reviews WHERE company_id=? ORDER BY updated_at DESC",(repo.COMPANY,))]
    events=[dict(r) for r in c.execute("SELECT e.event_id AS id,r.issue_code||'|'||r.source_ref AS issue_key,e.status,e.reason,e.created_at AS recorded_at,e.validation_id AS source_run FROM issue_review_events e JOIN issue_reviews r ON r.issue_id=e.issue_id WHERE e.company_id=? ORDER BY e.event_id DESC LIMIT 200",(repo.COMPANY,))]
    return dict(reviews=reviews,events=events)


def event(c,wp,kind,detail,actor='local-user',decision=None):
    repo.insert(c,'workpaper_events',company_id=repo.COMPANY,workpaper_id=wp,decision_id=decision,event_type=kind,detail_json=repo.dumps(detail),actor=actor,created_at=repo.now())


@transaction()
def generate(c,year,revision,candidates=None):
    from app.server.workpapers import _checks
    year=int(year);state,calculated,revision=context(c,year,revision)
    if closing_summary(c,year)['close']:raise ValueError('확정 사업연도에는 검증표를 생성할 수 없습니다.')
    canonical=candidates_for_year(calculated,year,int(state.get('fiscalEndMonth') or 12))
    from app.server.review_presentation import attach_review_details
    attach_review_details(state,calculated,canonical)
    if candidates is not None:
        if not isinstance(candidates,list) or len(candidates)>2000:raise ValueError('후보 데이터 형식 오류')
        expected={source_key(x):x for x in canonical};seen=set()
        for item in candidates:
            key=source_key(item)
            if key not in expected or key in seen:raise ValueError('서버 재계산 불일치: 후보 누락·중복')
            compare_candidate(item,expected[key]);seen.add(key)
        if seen!=set(expected):raise ValueError('서버 재계산 불일치: 검증표 후보 누락')
    run,fy=store_calculation(c,state,calculated,revision,year);proof=evidence(calculated,revision)
    for item in canonical:item.update(calculation=proof,adjustment_plan=build_plan(state,year,item))
    expected={source_key(x):x for x in canonical}
    for wp in paper_items(c,year):
        if wp['status']=='DRAFT' and (wp['source_revision']!=revision or wp['candidate']!=expected.get(source_key(wp['candidate']))):
            c.execute("UPDATE tax_workpapers SET status='SUPERSEDED' WHERE workpaper_id=?",(wp['id'],));event(c,wp['id'],'SUPERSEDED',{})
    count=0
    for item in canonical:
        active=[w for w in paper_items(c,year) if w['source_revision']==revision and source_key(w['candidate'])==source_key(item) and w['status'] in ('DRAFT','APPROVED')]
        if active:continue
        checks=_checks(state,year,item)+[dict(name='서버 독립 재계산',passed=True,note=calculated['engine_version'])]
        if not all(x['passed'] for x in checks if not x.get('manual')):raise ValueError('원천 데이터 검증 실패')
        kind=item['source_type'];table,idcol=('trade_events','trade_id') if kind=='trade' else ('valuation_events','valuation_id')
        source=c.execute(f'SELECT {idcol} FROM {table} WHERE company_id=? AND logical_key=? AND is_current=1 AND status=\'ACTIVE\'',(repo.COMPANY,f"{kind}:{item['source_id']}")).fetchone()
        if not source:raise ValueError('원천 SQL 연결이 없습니다.')
        cycles=[w for w in paper_items(c,year) if w['source_revision']==revision and source_key(w['candidate'])==source_key(item)]
        review=c.execute('SELECT review_id FROM company_rule_reviews WHERE company_id=? AND fiscal_year_id=? AND is_current=1',(repo.COMPANY,fy)).fetchone()
        identifier=repo.insert(c,'tax_workpapers',company_id=repo.COMPANY,fiscal_year_id=fy,run_id=run,evidence_state='VERIFIED',
                     **{idcol:source[0]},rule_review_id=review[0] if review else None,cycle=len(cycles)+1,status='DRAFT',
                     candidate_json=repo.dumps(item),checks_json=repo.dumps(checks),rule_snapshot_json=repo.dumps(item['adjustment_plan']),created_at=repo.now())
        event(c,identifier,'GENERATED',dict(calculation=proof));count+=1
    return dict(created=count,year=year,source_revision=revision,calculation=proof)


def prepared_rows(adjustments,item):
    from app.server.closing import _amount
    if not isinstance(adjustments,list) or len(adjustments)>20:raise ValueError('세무조정 행 형식 오류')
    result=[]
    for r in adjustments:
        amount=_amount(r.get('amount'));movement=_amount(r.get('reserve_movement',0));disposition=r.get('disposition');direction=r.get('direction')
        if direction not in ('익금산입','익금불산입','손금산입','손금불산입') or disposition not in ('유보','△유보','기타','기타사외유출','상여','배당') or amount<=0:raise ValueError('조정 방향·소득처분·금액 오류')
        if ((disposition=='유보' and movement<=0) or (disposition=='△유보' and movement>=0)
            or (disposition not in ('유보','△유보') and movement!=0) or (disposition in ('유보','△유보') and abs(movement)!=amount)):
            raise ValueError('유보 증감액과 조정금액·소득처분이 다릅니다.')
        basis=str(r.get('legal_basis','')).strip()
        if not basis:raise ValueError('각 조정 행의 법령·검토 근거를 입력하세요.')
        result.append(dict(asset_id=str(r.get('asset_id',item['asset_id'])),direction=direction,disposition=disposition,
                           amount=str(amount),reserve_movement=str(movement),legal_basis=basis))
        if 'label' in r:
            result[-1]['label']=normalize_adjustment_label(r['label'])
    return result


def paper_context(c,identifier):
    row=c.execute('SELECT w.*,y.fiscal_year,r.source_revision FROM tax_workpapers w JOIN fiscal_years y ON y.fiscal_year_id=w.fiscal_year_id JOIN calculation_runs r ON r.run_id=w.run_id WHERE w.company_id=? AND w.workpaper_id=?',(repo.COMPANY,int(identifier))).fetchone()
    if not row:raise ValueError('검증표를 찾을 수 없습니다.')
    return row


@transaction()
def approve(c,identifier,reviewer,note,adjustments,acknowledge):
    from app.server.workpapers import verify_candidate,verify_reserves
    if not str(reviewer).strip() or len(str(note).strip())<10:raise ValueError('검토자와 구체적인 검토 의견(10자 이상)이 필요합니다.')
    if acknowledge is not True:raise ValueError('법령 적용 및 계산 검토 확인이 필요합니다.')
    wp=paper_context(c,identifier)
    if wp['status']!='DRAFT':raise ValueError('승인 가능한 초안이 아닙니다.')
    year=wp['fiscal_year'];state,calculated,revision=context(c,year)
    if wp['source_revision']!=revision:raise ValueError('원본 데이터가 변경되었습니다. 검증표를 다시 생성하세요.')
    if closing_summary(c,year)['close']:raise ValueError('확정된 사업연도입니다.')
    item=repo.loads(wp['candidate_json']);expected=verify_candidate(item,calculated,revision,year,int(state.get('fiscalEndMonth') or 12),state)
    rows=prepared_rows(adjustments,item);verify_reserves(rows,expected);verify_adjustments(rows,expected,expected['adjustment_plan'])
    if not rows and any(Decimal(item[k]) for k in ('candidate_adjustment','reserve_change','oci_change')):raise ValueError('조정 행 없이 승인할 수 없습니다.')
    row_ids=[]
    for index,row in enumerate(rows,1):
        row_ids.append(repo.insert(c,'tax_adjustment_rows',company_id=repo.COMPANY,workpaper_id=wp['workpaper_id'],asset_id=int(row['asset_id']),row_no=index,
                       label=row.get('label','담당자 검토 조정'),direction=row['direction'],disposition=row['disposition'],amount=repo.decimal_text(row['amount']),
                       reserve_movement=repo.decimal_text(row['reserve_movement']),legal_basis=row['legal_basis'],created_at=repo.now()))
    proof=dict(calculation=evidence(calculated,revision),adjustment_plan=expected['adjustment_plan'],acknowledged=True)
    decision=repo.insert(c,'workpaper_decisions',company_id=repo.COMPANY,workpaper_id=wp['workpaper_id'],action='APPROVE',actor=reviewer,reason=note,
                         acknowledged=1,source_revision=revision,proof_json=repo.dumps(proof),approved_rows_json=repo.dumps(rows),created_at=repo.now())
    posted=0
    for row,rid in zip(rows,row_ids):
        if row['disposition'] not in ('유보','△유보'):continue
        target=next(a for a in state['assets'] if str(a['id'])==row['asset_id'])
        repo.insert(c,'reserve_entries',company_id=repo.COMPANY,fiscal_year_id=wp['fiscal_year_id'],asset_id=int(row['asset_id']),
                    workpaper_id=wp['workpaper_id'],decision_id=decision,adjustment_row_id=rid,entry_type='POST',
                    adjustment_key=f"{target['classification']}_{item['source_type'].upper()}",amount=repo.decimal_text(row['reserve_movement']),memo=note,recorded_at=repo.now())
        posted+=1
    c.execute("UPDATE tax_workpapers SET status='APPROVED' WHERE workpaper_id=?",(wp['workpaper_id'],));event(c,wp['workpaper_id'],'APPROVED',proof,reviewer,decision)
    return dict(id=int(identifier),status='APPROVED',reserve_posted=posted)


@transaction()
def cancel_approval(c,identifier,actor,reason):
    if not str(actor).strip() or len(str(reason).strip())<10:raise ValueError('정정 담당자와 정정 사유(10자 이상)가 필요합니다.')
    wp=paper_context(c,identifier)
    if wp['status']!='APPROVED':raise ValueError('취소 가능한 승인 건이 아닙니다.')
    if closing_summary(c,wp['fiscal_year'])['close']:raise ValueError('결산 확정 이후에는 취소할 수 없습니다.')
    original=c.execute("SELECT * FROM workpaper_decisions WHERE workpaper_id=? AND action='APPROVE'",(int(identifier),)).fetchone()
    decision=repo.insert(c,'workpaper_decisions',company_id=repo.COMPANY,workpaper_id=int(identifier),action='CANCEL',cancel_of_decision_id=original['decision_id'],
                        actor=actor,reason=reason,acknowledged=1,source_revision=repo.read_state(c)['revision'],proof_json=original['proof_json'],approved_rows_json=original['approved_rows_json'],created_at=repo.now())
    entries=c.execute("SELECT * FROM reserve_entries WHERE decision_id=? AND entry_type='POST'",(original['decision_id'],)).fetchall()
    for entry in entries:
        repo.insert(c,'reserve_entries',company_id=repo.COMPANY,fiscal_year_id=wp['fiscal_year_id'],asset_id=entry['asset_id'],workpaper_id=int(identifier),
                    decision_id=decision,adjustment_row_id=entry['adjustment_row_id'],entry_type='REVERSAL',reversal_of_entry_id=entry['entry_id'],
                    adjustment_key=entry['adjustment_key'],amount=repo.decimal_text(-Decimal(entry['amount'])),memo=reason,recorded_at=repo.now())
    c.execute("UPDATE tax_workpapers SET status='CANCELLED' WHERE workpaper_id=?",(int(identifier),));event(c,int(identifier),'CANCELLED',dict(prior_approval=repo.loads(original['approved_rows_json'])),actor,decision)
    return dict(id=int(identifier),status='CANCELLED',reversed_entries=len(entries))


@transaction()
def close_year(c,year,reviewer,note,acknowledge_warnings=False,positions=None):
    from app.server.workpapers import verify_candidate,verify_reserves
    year=int(year)
    if not str(reviewer).strip() or not str(note).strip():raise ValueError('확정자와 검토 메모가 필요합니다.')
    current=closing_summary(c,year)
    if current['close']:raise ValueError('이미 확정된 사업연도입니다.')
    state,calculated,revision=context(c,year);proof=evidence(calculated,revision)
    run,fy=store_calculation(c,state,calculated,revision,year)
    validation=c.execute('SELECT * FROM validation_runs WHERE company_id=? AND fiscal_year_id=? AND run_id=? AND source_revision=? ORDER BY validation_id DESC LIMIT 1',(repo.COMPANY,fy,run,revision)).fetchone()
    if not validation:raise ValueError('현재 데이터 버전으로 자동 검증을 먼저 실행하세요.')
    previous=repo.loads(validation['result_json']);fresh=validation_result(c,state,year,calculated)
    if previous.get('calculation')!=proof:raise ValueError('자동 검증 계산 근거가 다릅니다.')
    if previous['summary']['ERROR'] or fresh['summary']['ERROR']:raise ValueError('검증 오류가 남아 있어 결산 확정이 차단됩니다.')
    if (previous['summary']['WARNING'] or fresh['summary']['WARNING']) and not acknowledge_warnings:raise ValueError('검증 경고를 검토하고 승인 여부를 명시하세요.')
    papers=paper_items(c,year)
    if any(w['status']=='DRAFT' for w in papers):raise ValueError('미승인 검증표가 있어 결산 확정이 차단됩니다.')
    approved=[w for w in papers if w['status']=='APPROVED'];month=int(state.get('fiscalEndMonth') or 12)
    required={source_key(i) for i in candidates_for_year(calculated,year,month)}
    if required-{source_key(w['candidate']) for w in approved}:raise ValueError('미작성 검증표가 있습니다.')
    for wp in approved:
        if wp['source_revision']!=revision:raise ValueError('원본 데이터가 변경된 검증표가 있습니다.')
        expected=verify_candidate(wp['candidate'],calculated,revision,year,month,state);approval=wp['approved']
        if approval['calculation']!=proof or approval['adjustment_plan']!=expected['adjustment_plan']:raise ValueError('승인 계산·규칙 근거 불일치')
        rows=approval['adjustments'];verify_reserves(rows,expected);verify_adjustments(rows,expected,expected['adjustment_plan'])
        stored=c.execute('SELECT * FROM tax_adjustment_rows WHERE workpaper_id=? ORDER BY row_no',(wp['id'],)).fetchall()
        if len(stored)!=len(rows):raise ValueError('승인 조정 행 누락')
        posts=c.execute("SELECT * FROM reserve_entries WHERE workpaper_id=? AND entry_type='POST' ORDER BY entry_id",(wp['id'],)).fetchall()
        expected_posts=[]
        for row,actual in zip(rows,stored):
            if 'label' in row and row['label']!=actual['label']:raise ValueError('승인 조정 원인이 실제 행과 다릅니다.')
            for field in ('direction','disposition','legal_basis'):
                if row[field]!=actual[field]:raise ValueError('승인 조정 행과 실제 행이 다릅니다.')
            if (int(row['asset_id'])!=actual['asset_id'] or Decimal(row['amount'])!=Decimal(actual['amount'])
                or Decimal(row['reserve_movement'])!=Decimal(actual['reserve_movement'])):raise ValueError('승인 조정 금액·자산이 다릅니다.')
            if row['disposition'] in ('유보','△유보'):expected_posts.append(actual)
        if len(posts)!=len(expected_posts):raise ValueError('승인 유보 전기 이력 불일치')
        for post,actual in zip(posts,expected_posts):
            if post['adjustment_row_id']!=actual['adjustment_row_id'] or post['asset_id']!=actual['asset_id'] or Decimal(post['amount'])!=Decimal(actual['reserve_movement']):raise ValueError('승인 유보 전기 금액·자산 불일치')
    compare_positions(positions,calculated)
    if not current['prior_closed'] and c.execute('SELECT 1 FROM reserve_entries e JOIN fiscal_years y ON y.fiscal_year_id=e.fiscal_year_id WHERE e.company_id=? AND y.fiscal_year<? LIMIT 1',(repo.COMPANY,year)).fetchone():raise ValueError('직전 결산이 확정되지 않았습니다.')
    for p in calculated['positions']:
        actual=sum((Decimal(v) for k,v in current['ending'].items() if k.split('|')[0]==str(p['assetId'])),Decimal(0))
        if actual!=Decimal(p['tax'])-Decimal(p['book']):raise ValueError('자산별 기말 유보 대사 불일치')
    for pool in calculated['stockPools']:
        ids={a['id'] for a in state['assets'] if a['stock_code']==pool['stock_code']}
        if sum((Decimal(p['tax']) for p in calculated['positions'] if p['assetId'] in ids),Decimal(0))!=Decimal(pool['tax']):raise ValueError('종목 통합 세무원가 배분 불일치')
    prior=c.execute('SELECT z.close_id FROM fiscal_closes z JOIN fiscal_years y ON y.fiscal_year_id=z.fiscal_year_id WHERE z.company_id=? AND y.fiscal_year=?',(repo.COMPANY,year-1)).fetchone()
    policy=dict(legacy=state.get('fvociPolicy'),years=[r for r in state.get('fvociPolicyYears',[]) if r['effectiveYear']<=year])
    identifier=repo.insert(c,'fiscal_closes',company_id=repo.COMPANY,fiscal_year_id=fy,run_id=run,validation_id=validation['validation_id'],prior_close_id=prior[0] if prior else None,
                         source_revision=revision,evidence_state='VERIFIED',opening_json=repo.dumps(current['opening']),movements_json=repo.dumps(current['movements']),ending_json=repo.dumps(current['ending']),
                         positions_json=repo.dumps(calculated['positions']),policy_snapshot_json=repo.dumps(policy),proof_json=repo.dumps(proof),reviewer=reviewer,note=note,closed_at=repo.now())
    repo.insert(c,'closing_events',company_id=repo.COMPANY,fiscal_year_id=fy,close_id=identifier,event_type='CLOSE',detail_json=repo.dumps(dict(calculation=proof,positions=calculated['positions'],fvoci_policy_snapshot=policy)),actor=reviewer,created_at=repo.now())
    return closing_summary(c,year)
