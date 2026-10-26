import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from app.server import storage, workpapers, closing
from app.server.calculation import calculate_year, candidates_for_year, evidence
from app.server.reconciliation import run_validation


class ServerControlTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        old = storage.DB_PATH;storage.DB_PATH = Path(tmp.name) / 'test.sqlite3'
        self.addCleanup(setattr,storage,'DB_PATH',old)
        self.state = dict(master=[],assets=[dict(id=1,stock_code='A',classification='FVOCI')],
            trades=[dict(id=1,assetId=1,date='2026-01-01',type='BUY',qty=10,price=10,amount=100),
                    dict(id=2,assetId=1,date='2026-11-01',type='SELL',qty=10,price=18,amount=180)],
            valuations=[dict(id=1,assetId=1,date='2026-06-30',qty=10,price=14,fairValue=140)],
            adjustments=[],taxPolicyYears=[],fvociPolicy=dict(disposal='TRANSFER'))
        storage.save_state(self.state,0)

    def calculated(self, year=2026):
        return calculate_year(self.state,year)

    def adjustments(self, item):
        result=[]
        for effect in item['allocation_effects']:
            movement=Decimal(effect['reserveChange'])
            if movement:
                result.append(dict(asset_id=effect['assetId'],direction='익금산입' if movement>0 else '손금산입',
                    disposition='유보' if movement>0 else '△유보',amount=str(abs(movement)),
                    reserve_movement=str(movement),legal_basis='검토 사례의 원천 및 장부 차이 확인'))
        other=Decimal(item['candidate_adjustment'])-sum((Decimal(x['reserve_movement']) for x in result),Decimal(0))
        if other:
            result.append(dict(direction='익금산입' if other>0 else '손금산입',disposition='기타',
                               amount=str(abs(other)),reserve_movement='0',legal_basis='사례의 과세손익 연결 검토'))
        return result

    def approve_all(self, year=2026, revision=1):
        workpapers.generate(year,revision)
        for wp in workpapers.list_workpapers(year)['items']:
            if wp['status']=='DRAFT':
                workpapers.approve(wp['id'],'reviewer','원천과 계산 근거를 확인한 테스트 승인',self.adjustments(wp['candidate']),True)

    def test_validation_uses_server_and_records_client_mismatch(self):
        good=self.calculated()['positions']
        for field in ('book','tax','qty','reserve','oci'):
            bad=copy.deepcopy(good);bad[0][field]=Decimal(str(bad[0][field]))+1
            bad[0][field]=str(bad[0][field])
            with self.subTest(field=field):
                result=run_validation(2026,1,{'positions':bad})
                self.assertIn('SERVER_CALC_MISMATCH',{i['code'] for i in result['issues']})
                self.assertEqual(result['positions'],good)
        for positions in ([],good+good):
            self.assertTrue(run_validation(2026,1,{'positions':positions})['summary']['ERROR'])
        self.assertEqual(run_validation(2026,1,{'positions':good})['calculation'],evidence(self.calculated(),1))

    def test_generated_candidates_reject_changed_or_missing_numbers(self):
        expected=candidates_for_year(self.calculated(),2026,12)
        for field in ('book_pl','tax_pl','oci_change','reserve_change','oci_equity_transfer','book_basis','tax_basis'):
            bad=copy.deepcopy(expected);bad[0][field]=str(Decimal(bad[0][field])+1)
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError,'재계산 불일치'):workpapers.generate(2026,1,bad)
        for bad in ([],expected[:-1],expected+expected):
            with self.assertRaisesRegex(ValueError,'재계산 불일치'):workpapers.generate(2026,1,bad)
        self.assertEqual(workpapers.list_workpapers(2026)['items'],[])
        self.assertEqual(workpapers.generate(2026,1,expected)['created'],2)

    def test_approval_checks_reserve_amount_and_source_snapshot_atomically(self):
        workpapers.generate(2026,1)
        wp=workpapers.list_workpapers(2026)['items'][0]
        rows=self.adjustments(wp['candidate'])
        bad=copy.deepcopy(rows);bad[0]['amount']='41';bad[0]['reserve_movement']='-41'
        with self.assertRaisesRegex(ValueError,'유보 증감액'):workpapers.approve(wp['id'],'reviewer','검토 의견을 기록하는 테스트',bad,True)
        self.assertEqual(closing.summary(2026)['entries'],[])
        self.assertEqual(workpapers.list_workpapers(2026)['items'][0]['status'],'DRAFT')
        forged=copy.deepcopy(wp['candidate']);forged['oci_change']='999'
        with storage.connect() as c:c.execute('UPDATE tax_workpapers SET candidate_json=? WHERE id=?',(json.dumps(forged),wp['id']))
        with self.assertRaisesRegex(ValueError,'재계산 불일치'):workpapers.approve(wp['id'],'reviewer','검토 의견을 기록하는 테스트',rows,True)

    def test_stale_validation_and_workpapers_cannot_approve_or_close(self):
        self.approve_all();run_validation(2026,1)
        storage.save_state({**self.state,'fvociPolicy':{'disposal':'RETAIN'}},1)
        with self.assertRaisesRegex(ValueError,'자동 검증'):closing.close_year(2026,'reviewer','최종 검토',True,self.calculated()['positions'])
        with self.assertRaisesRegex(ValueError,'버전'):run_validation(2026,1)
        workpapers.generate(2026,2)
        fresh=next(w for w in workpapers.list_workpapers(2026)['items'] if w['source_revision']==2)
        storage.save_state({**self.state,'fvociPolicy':{'disposal':'TRANSFER'}},2)
        with self.assertRaisesRegex(ValueError,'원본 데이터'):workpapers.approve(fresh['id'],'reviewer','검토 의견을 기록하는 테스트',self.adjustments(fresh['candidate']),True)

    def test_legacy_evidence_requires_regeneration_without_deleting_history(self):
        workpapers.generate(2026,1)
        wp=workpapers.list_workpapers(2026)['items'][0]
        legacy=copy.deepcopy(wp['candidate']);legacy.pop('calculation')
        with storage.connect() as c:c.execute('UPDATE tax_workpapers SET candidate_json=? WHERE id=?',(json.dumps(legacy),wp['id']))
        with self.assertRaisesRegex(ValueError,'재생성'):workpapers.approve(wp['id'],'reviewer','검토 의견을 기록하는 테스트',self.adjustments(legacy),True)
        workpapers.generate(2026,1)
        items=workpapers.list_workpapers(2026)['items']
        self.assertEqual(items[0]['status'],'SUPERSEDED')
        self.assertEqual(len(items),3)
        self.assertEqual(workpapers.generate(2026,1)['created'],0)

    def test_close_rejects_legacy_validation_and_tampered_posting(self):
        self.approve_all()
        storage.save_validation({'year':2026,'summary':{'ERROR':0,'WARNING':0}},1)
        with self.assertRaisesRegex(ValueError,'다시 실행'):closing.close_year(2026,'reviewer','검토',True,self.calculated()['positions'])
        run_validation(2026,1)
        with storage.connect() as c:c.execute("UPDATE reserve_entries SET amount='-39' WHERE amount='-40.00'")
        with self.assertRaisesRegex(ValueError,'전기 금액'):closing.close_year(2026,'reviewer','검토',True,self.calculated()['positions'])
        self.assertIsNone(closing.summary(2026)['close'])

    def test_mixed_pool_allocations_approve_by_target_asset_and_close(self):
        self.state['assets'].append(dict(id=2,stock_code='A',classification='FVTPL'))
        self.state['trades'].insert(1,dict(id=3,assetId=2,date='2026-02-01',type='BUY',qty=10,price=30,amount=300))
        self.state['valuations'].append(dict(id=2,assetId=2,date='2026-12-31',qty=10,price=16,fairValue=160))
        storage.save_state(self.state,1)
        workpapers.generate(2026,2)
        buy=next(w for w in workpapers.list_workpapers(2026)['items'] if w['candidate']['event']=='BUY')
        self.assertEqual([(e['assetId'],e['reserveChange']) for e in buy['candidate']['allocation_effects']],[(1,'100.00'),(2,'-100.00')])
        wrong=self.adjustments(buy['candidate']);wrong[1]['asset_id']=1
        with self.assertRaisesRegex(ValueError,'유보 증감액'):workpapers.approve(buy['id'],'reviewer','자산별 배분 검토 기록 테스트',wrong,True)
        self.approve_all(revision=2)
        run_validation(2026,2)
        result=closing.close_year(2026,'reviewer','혼합 보유 최종 검토',True,self.calculated()['positions'])
        self.assertTrue(result['close'])
        ending=result['ending']
        self.assertEqual(sum(Decimal(v) for k,v in ending.items() if k.startswith('2|')),Decimal(40))

    def test_two_year_carryforward_and_oci_retain_policy(self):
        self.state['fvociPolicy']={'disposal':'RETAIN'}
        self.state['valuations']=[dict(id=1,assetId=1,date='2025-12-31',qty=10,price=14,fairValue=140)]
        self.state['trades'][0]['date']='2025-01-01'
        storage.save_state(self.state,1)
        for year in (2025,2026):
            self.approve_all(year,2);run_validation(year,2)
            result=closing.close_year(year,'reviewer','사업연도 이월 대사 검토',True,self.calculated(year)['positions'])
            self.assertTrue(result['close'])
        self.assertEqual(self.calculated()['positions'][0]['oci'],'80.00')
        self.assertEqual(sum(Decimal(v) for v in result['ending'].values()),Decimal(0))
        self.assertTrue(result['prior_closed'])

    def test_cancel_regenerate_reapprove_and_snapshot_recorded_on_close(self):
        self.approve_all()
        wp=workpapers.list_workpapers(2026)['items'][-1]
        workpapers.cancel_approval(wp['id'],'reviewer','처분 검토 근거 재확인 테스트')
        self.approve_all();run_validation(2026,1)
        closing.close_year(2026,'reviewer','최종 대사 근거 확인',True,self.calculated()['positions'])
        with storage.connect() as c:
            detail=json.loads(c.execute("SELECT detail_json FROM closing_events WHERE event='CLOSE'").fetchone()[0])
        self.assertEqual(detail['calculation'],evidence(self.calculated(),1))
        self.assertEqual(detail['positions'],self.calculated()['positions'])
