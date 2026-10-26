import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from app.server import storage, workpapers, closing
from app.server.adjustment_rules import build_plan, verify_adjustments, PROFILE, signed_total
from app.server.calculation import calculate, calculate_year
from app.server.reconciliation import run_validation

CASES = json.loads((Path(__file__).parents[1]/'fixtures/tax_adjustment_2026.json').read_text())['cases']


def state_for(case, policy='TRANSFER'):
    qty = case.get('qty',5)
    state = dict(master=[],assets=[dict(id=1,stock_code='005930',asset_name='검토 보통주',classification=case['classification'])],
                 trades=[dict(id=1,assetId=1,date='2026-01-01',type='BUY',qty=10,price=10,amount=100)],
                 valuations=[dict(id=1,assetId=1,date='2026-06-30',price=case['price'],qty=10,fairValue=case['price']*10)],
                 taxPolicy={'method':case.get('method','MOVING')},taxPolicyYears=[],adjustments=[],fvociPolicy={'disposal':policy},
                 taxAdjustmentScope=dict(enabled=True,confirmed=True,year=2026,profile=PROFILE,
                                         reviewer='범위 확인자',note='일반 내국법인 보통주 정상 장내거래, 제외요건 없음 확인',exceptions=[]))
    if case['event']=='SELL':
        state['trades'].append(dict(id=2,assetId=1,date='2026-07-01',type='SELL',qty=qty,amount=case['proceeds'],price=case['proceeds']/qty))
    remaining = 10-(qty if case['event']=='SELL' else 0)
    if case.get('future_buy'):
        state['trades'].append(dict(id=3,assetId=1,date='2026-08-01',type='BUY',qty=10,amount=case['future_buy'],price=case['future_buy']/10))
        remaining+=10
    if remaining:
        state['valuations'].append(dict(id=2,assetId=1,date='2026-12-31',price=case['price'],qty=remaining,fairValue=remaining*case['price']))
    return state


def compact(rows):
    return [(r['direction'],r['disposition'],Decimal(r['amount']),Decimal(r['reserve_movement'])) for r in rows]


class TaxAdjustmentRulesTests(unittest.TestCase):
    def database(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        old=storage.DB_PATH;storage.DB_PATH=Path(tmp.name)/'rules.sqlite3'
        self.addCleanup(setattr,storage,'DB_PATH',old)

    def test_hand_answers_and_policies_through_approval_ledger_and_close(self):
        for case in CASES:
            answers=[]
            for policy in ('TRANSFER','RETAIN'):
                with self.subTest(case=case['id'],policy=policy):
                    self.database();state=state_for(case,policy);storage.save_state(state,0)
                    workpapers.generate(2026,1)
                    papers=workpapers.list_workpapers(2026)['items']
                    target=next(w for w in papers if w['candidate']['event']==case['event'] and w['candidate']['source_id']==(2 if case['event']=='SELL' else 1))
                    plan=target['candidate']['adjustment_plan']
                    self.assertEqual(plan['status'],'REVIEW_REQUIRED',plan['reasons'])
                    expected=[(direction,disposition,Decimal(str(value)),Decimal(str(movement))) for direction,disposition,value,movement in case['rows']]
                    self.assertEqual(compact(plan['rows']),expected)
                    answers.append(plan['rows'])
                    for wp in papers:
                        candidate=wp['candidate'];rows=candidate['adjustment_plan']['rows']
                        self.assertEqual(signed_total(rows),Decimal(candidate['candidate_adjustment']))
                        # Human acknowledgement remains mandatory even for exact generated rows.
                        with self.assertRaises(ValueError):workpapers.approve(wp['id'],'검토자','근거와 예외를 확인한 검토',rows,False)
                        workpapers.approve(wp['id'],'검토자','근거와 예외를 확인한 검토',rows,True)
                    result=run_validation(2026,1)
                    self.assertEqual(result['summary']['ERROR'],0,result['issues'])
                    calculated=calculate_year(state,2026)
                    closed=closing.close_year(2026,'검토자','원인별 조정행과 원장 결산 대사',True,calculated['positions'])
                    self.assertEqual(sum(map(Decimal,closed['ending'].values())),Decimal(calculated['positions'][0]['reserve']))
                    with storage.connect() as c:
                        approved=json.loads(c.execute('SELECT approved_json FROM tax_workpapers WHERE id=?',(target['id'],)).fetchone()[0])
                    self.assertEqual(approved['adjustment_plan'],plan)
                    self.assertEqual(compact(approved['adjustments']),expected)
            self.assertEqual(answers[0],answers[1],case['id']+' transfer must not be taxed twice')

    def test_scope_exclusions_do_not_produce_plug_rows(self):
        baseline=state_for(CASES[2])
        variations=[]
        s=copy.deepcopy(baseline);s.pop('taxAdjustmentScope');variations.append(s)
        for key,value in [('confirmed',False),('exceptions',['부도']),('reviewer',''),('profile','OTHER')]:
            s=copy.deepcopy(baseline);s['taxAdjustmentScope'][key]=value;variations.append(s)
        s=copy.deepcopy(baseline);s['fiscalEndMonth']=3;variations.append(s)
        s=copy.deepcopy(baseline);s['assets'].append(dict(id=2,stock_code='005930',classification='FVTPL'));variations.append(s)
        for key,value in [('instrument_type','BOND'),('issuer_country','US'),('listed',False)]:
            s=copy.deepcopy(baseline);s['assets'][0][key]=value;variations.append(s)
        for key in ('fees','related_party','impairment_exception','corporate_action','deferred_tax'):
            s=copy.deepcopy(baseline);s['trades'][0][key]=1;variations.append(s)
        for state in variations:
            item=next(e for e in calculate(state,'2026-12-31')['ledger'] if e['event']=='SELL')
            plan=build_plan(state,2026,item)
            self.assertEqual(plan['status'],'MANUAL_REQUIRED');self.assertEqual(plan['rows'],[])
            self.assertTrue(plan['reasons'])
        item=next(e for e in calculate_year(baseline,2026)['ledger'] if e['event']=='SELL')
        self.assertEqual(build_plan(baseline,2027,item)['rows'],[])

    def test_enabled_exception_cannot_approve_and_old_close_allows_next_year_scope(self):
        self.database();state=state_for(CASES[2]);state['taxAdjustmentScope']['exceptions']=['회생']
        storage.save_state(state,0);workpapers.generate(2026,1)
        wp=next(w for w in workpapers.list_workpapers(2026)['items'] if w['candidate']['event']=='SELL')
        rows=[dict(asset_id='1',direction='익금산입',disposition=disp,amount='20',reserve_movement=move,legal_basis='예외 적용은 이 자동규칙으로 승인 불가') for disp,move in [('기타','0'),('유보','20')]]
        with self.assertRaisesRegex(ValueError,'적용 제외'):
            workpapers.approve(wp['id'],'검토자','예외에 숫자를 맞춰 승인하는 것 차단',rows,True)
        self.assertEqual(closing.summary(2026)['entries'],[])
        with storage.connect() as c:
            c.execute("INSERT INTO fiscal_closes VALUES(2025,1,1,'test','{}','{}','{}','검토자','범위 변경 통제 테스트')")
            changed=copy.deepcopy(state);changed['taxAdjustmentScope']['exceptions']=[]
            closing.guard_state_changes(state,changed,c)
            changed['taxAdjustmentScope']['year']=2025
            with self.assertRaisesRegex(ValueError,'재개방'):closing.guard_state_changes(state,changed,c)

    def test_wrong_rows_block_without_posting_even_when_net_and_reserve_match(self):
        self.database();state=state_for(CASES[2]);storage.save_state(state,0);workpapers.generate(2026,1)
        wp=next(w for w in workpapers.list_workpapers(2026)['items'] if w['candidate']['event']=='SELL')
        plan=wp['candidate']['adjustment_plan'];good=plan['rows']
        variants=[good[:-1],good+good]
        wrong=copy.deepcopy(good);wrong[0]['direction']='손금산입';variants.append(wrong)
        wrong=copy.deepcopy(good);wrong[0]['disposition']='유보';wrong[0]['reserve_movement']='20';variants.append(wrong)
        # Old netted formula: correct net income / reserves, but loses stage evidence.
        variants.append([dict(asset_id='1',direction='익금산입',disposition=disp,amount='20',reserve_movement=move,legal_basis='상계하면 안 되는 조정행 테스트') for disp,move in [('기타','0'),('유보','20')]])
        wrong=copy.deepcopy(good);wrong[1]['direction']='익금불산입';variants.append(wrong)
        for rows in variants:
            with self.subTest(rows=rows),self.assertRaises(ValueError):
                workpapers.approve(wp['id'],'검토자','잘못된 조정행을 차단하는 검토',rows,True)
            self.assertEqual(closing.summary(2026)['entries'],[])

    def test_plan_tampering_stale_version_and_scope_audit(self):
        self.database();state=state_for(CASES[2]);storage.save_state(state,0);workpapers.generate(2026,1)
        wp=workpapers.list_workpapers(2026)['items'][0]
        forged=copy.deepcopy(wp['candidate']);forged['adjustment_plan']['version']='forged'
        with storage.connect() as c:c.execute('UPDATE tax_workpapers SET candidate_json=? WHERE id=?',(json.dumps(forged),wp['id']))
        with self.assertRaisesRegex(ValueError,'규칙'):workpapers.approve(wp['id'],'검토자','변조된 조정 규칙 근거 차단',wp['candidate']['adjustment_plan']['rows'],True)
        workpapers.generate(2026,1)
        self.assertEqual(workpapers.list_workpapers(2026)['items'][0]['status'],'SUPERSEDED')
        self.assertTrue(any(e['entity_key']=='taxAdjustmentScope' for e in storage.read_audit()))

    def test_close_rechecks_disposition_and_scope_cannot_change_after_close(self):
        self.database();state=state_for(CASES[0]);storage.save_state(state,0);workpapers.generate(2026,1)
        for wp in workpapers.list_workpapers(2026)['items']:
            workpapers.approve(wp['id'],'검토자','규칙과 적용요건의 독립 검토',wp['candidate']['adjustment_plan']['rows'],True)
        run_validation(2026,1);calculated=calculate_year(state,2026)
        with storage.connect() as c:
            wp=c.execute("SELECT id,approved_json FROM tax_workpapers ORDER BY id LIMIT 1").fetchone()
            original=wp['approved_json'];approval=json.loads(original)
            approval['adjustments'][1]['direction']='익금불산입'
            c.execute('UPDATE tax_workpapers SET approved_json=? WHERE id=?',(json.dumps(approval),wp['id']))
        with self.assertRaisesRegex(ValueError,'복수 행'):
            closing.close_year(2026,'검토자','금액만 맞는 소득처분 오류 차단',True,calculated['positions'])
        with storage.connect() as c:c.execute('UPDATE tax_workpapers SET approved_json=? WHERE id=?',(original,wp['id']))
        closing.close_year(2026,'검토자','원래 근거 복구 후 결산 대사',True,calculated['positions'])
        state['taxAdjustmentScope']['note']='사후 변경'
        with self.assertRaisesRegex(ValueError,'재개방'):storage.save_state(state,1)
