import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from app.server import storage, closing, workpapers
from app.server.calculation import calculate_year
from app.server.fvoci_policy import policy_for_year
from app.server.reconciliation import run_validation
from tests.python import test_server_controls as control_helpers

ROOT=Path(__file__).resolve().parents[2]
CASES=json.loads((ROOT/'tests/fixtures/fvoci_policy_years.json').read_text())['cases']


def policy(year,disposal):
    return dict(effectiveYear=year,disposal=disposal,reviewer='정책 확인자',reason='해당 사업연도 이후 처분 정책 검토',at='2026-10-26T00:00:00Z')


def state_for(case,annual=True,march=False):
    state=dict(master=[],assets=[dict(id=1,stock_code='A',asset_name='이월 검증 종목',classification='FVOCI')],
               trades=[dict(id=1,assetId=1,date='2025-01-01',type='BUY',qty=10,price=10,amount=100),
                       dict(id=2,assetId=1,date='2026-07-01',type='SELL',qty=4,price=18,amount=72),
                       dict(id=3,assetId=1,date='2027-07-01',type='SELL',qty=6,price=15,amount=90)],
               valuations=[dict(id=1,assetId=1,date='2025-12-31',qty=10,price=14,fairValue=140),
                           dict(id=2,assetId=1,date='2026-12-31',qty=6,price=14,fairValue=84)],
               adjustments=[],taxPolicyYears=[],taxPolicy={'method':'MOVING'},fiscalEndMonth=12,
               fvociPolicy=dict(disposal=case['baseline'],reviewer='기존 확인자',reason='기존 정책',history=[]))
    if annual:
        state['fvociPolicyYears']=[policy(y,p) for y,p in reversed(case['policies'])]
    if march:
        state['fiscalEndMonth']=3
        for t,date in zip(state['trades'],('2025-04-01','2026-04-01','2027-04-01')):t['date']=date
        for v,date in zip(state['valuations'],('2026-03-31','2027-03-31')):v['date']=date
        for p in state.get('fvociPolicyYears',[]):p['effectiveYear']+=1
    return state


class FvociPolicyYearsTests(unittest.TestCase):
    def database(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        old=storage.DB_PATH;storage.DB_PATH=Path(tmp.name)/'years.sqlite3'
        self.addCleanup(setattr,storage,'DB_PATH',old)

    def approve_close(self,state,year,revision):
        workpapers.generate(year,revision)
        for wp in workpapers.list_workpapers(year)['items']:
            if wp['status']=='DRAFT':
                rows=control_helpers.ServerControlTests.adjustments(self,wp['candidate'])
                workpapers.approve(wp['id'],'검토자','사업연도 정책과 이월잔액 검토',rows,True)
        calculated=calculate_year(state,year)
        result=run_validation(year,revision,{'positions':calculated['positions']})
        self.assertEqual(result['summary']['ERROR'],0,result['issues'])
        result=closing.close_year(year,'검토자','사업연도별 정책 이월 검토',True,calculated['positions'])
        with storage.connect() as c:
            detail=json.loads(c.execute("SELECT detail_json FROM closing_events WHERE fiscal_year=? AND event='CLOSE'",(year,)).fetchone()[0])
        self.assertEqual(detail['fvoci_policy_snapshot'],{'legacy':state.get('fvociPolicy'),
                         'years':[r for r in state.get('fvociPolicyYears',[]) if r['effectiveYear']<=year]})
        return result

    def test_manual_annual_answers_and_march_fiscal_boundaries(self):
        for case in CASES:
            for march in (False,True):
                state=state_for(case,march=march);original=copy.deepcopy(state)
                for year,qty,book,tax,reserve,oci,active,retained in case['checks']:
                    result=calculate_year(state,year+int(march));p=result['positions'][0]
                    for field,value in dict(qty=qty,book=book,tax=tax,reserve=reserve,oci=oci,activeOCI=active,retainedDisposalOCI=retained).items():
                        with self.subTest(case=case['id'],march=march,year=year,field=field):
                            self.assertEqual(Decimal(str(p[field])),Decimal(value))
                for source,transfer,disposal in case['transfers']:
                    row=next(e for e in result['ledger'] if e['source_type']=='trade' and e['source_id']==source)
                    self.assertEqual(Decimal(row['oci_equity_transfer']),Decimal(transfer))
                    self.assertEqual(row['fvoci_disposal_policy'],disposal)
                self.assertEqual(state,original)
                self.assertEqual(policy_for_year(state,2030),case['policies'][-1][1])

    def test_future_policy_preserves_closed_records_proof_and_carryforward(self):
        for case in CASES:
            with self.subTest(case=case['id']):
                self.database();state=state_for(case,annual=False);storage.save_state(state,0)
                original_legacy=copy.deepcopy(state['fvociPolicy'])
                self.approve_close(state,2025,1)
                # Simulate a pre-upgrade event: missing snapshots must not be backfilled.
                with storage.connect() as c:
                    record=c.execute("SELECT id,detail_json FROM closing_events WHERE event='CLOSE'").fetchone()
                    old_detail=json.loads(record['detail_json']);old_detail.pop('fvoci_policy_snapshot')
                    c.execute('UPDATE closing_events SET detail_json=? WHERE id=?',(json.dumps(old_detail),record['id']))
                for revision,(year,disposal) in enumerate(case['policies'],2):
                    with storage.connect() as c:
                        frozen={table:[dict(r) for r in c.execute(f'SELECT * FROM {table} ORDER BY 1')]
                                for table in ('fiscal_closes','closing_events','tax_workpapers','reserve_entries')}
                    prior=calculate_year(state,year-1)
                    state.setdefault('fvociPolicyYears',[]).append(policy(year,disposal))
                    storage.save_state(state,revision-1)
                    self.assertEqual(calculate_year(state,year-1),prior,'including old calculation digest')
                    self.assertEqual(state['fvociPolicy'],original_legacy)
                    with storage.connect() as c:
                        for table,rows in frozen.items():
                            self.assertEqual([dict(r) for r in c.execute(f'SELECT * FROM {table} ORDER BY 1')],rows)
                    expected_opening=closing.summary(year-1)['ending']
                    result=self.approve_close(state,year,revision)
                    self.assertEqual(result['opening'],expected_opening)
                    self.assertEqual(sum(map(Decimal,result['ending'].values())),Decimal(-24 if year==2026 else 0))
                self.assertEqual(Decimal(calculate_year(state,2027)['positions'][0]['oci']),Decimal(case['checks'][-1][5]))
                audit=storage.read_audit()
                self.assertTrue(any(e['entity']=='fvociPolicyYears' and e['entity_key']=='2027' for e in audit))

    def test_closed_policy_cannot_be_added_edited_deleted_or_backdated(self):
        self.database();state=state_for(CASES[0]);storage.save_state(state,0)
        self.approve_close(state,2025,1);self.approve_close(state,2026,1)
        variants=[]
        for change in ('disposal','reason','reviewer','history'):
            s=copy.deepcopy(state);row=next(r for r in s['fvociPolicyYears'] if r['effectiveYear']==2026)
            row[change]='TRANSFER' if change=='disposal' else ([] if change=='history' else '사후 변경');variants.append(s)
        s=copy.deepcopy(state);s['fvociPolicyYears']=[r for r in s['fvociPolicyYears'] if r['effectiveYear']!=2026];variants.append(s)
        s=copy.deepcopy(state);s['fvociPolicyYears'].append(policy(2024,'RETAIN'));variants.append(s)
        for changed in variants:
            with self.assertRaisesRegex(ValueError,'확정 결산'):storage.save_state(changed,1)
        # Future policy can still be corrected, with immutable storage audit.
        changed=copy.deepcopy(state);next(r for r in changed['fvociPolicyYears'] if r['effectiveYear']==2027)['reason']='미확정 연도 정책 근거 보완'
        storage.save_state(changed,1)
        self.assertTrue(any(e['entity']=='fvociPolicyYears' and e['action']=='UPDATE' for e in storage.read_audit()))

    def test_invalid_schedules_fail_before_storage_or_calculation(self):
        self.database();base=state_for(CASES[0],annual=False)
        for rows in (None,{},[policy(True,'TRANSFER')],[policy('2026','TRANSFER')],
                     [policy(2026,'OTHER')],[policy(2026,'RETAIN'),policy(2026,'TRANSFER')],
                     [{**policy(2026,'TRANSFER'),'reason':''}], [{**policy(2026,'TRANSFER'),'reviewer':''}]):
            state={**base,'fvociPolicyYears':rows}
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):storage.save_state(state,0)
                with self.assertRaises(ValueError):calculate_year(state,2026)
        self.assertEqual(storage.read_state()['revision'],0)

    def test_unconfirmed_default_and_legacy_baseline_do_not_migrate_implicitly(self):
        state=state_for(CASES[0],annual=False);state.pop('fvociPolicy')
        self.assertEqual(policy_for_year(state,2025),'TRANSFER')
        self.assertEqual(calculate_year(state,2026),calculate_year({**state,'fvociPolicyYears':[]},2026))

    def test_changed_annual_policy_invalidates_open_workpaper_revision(self):
        self.database();state=state_for(CASES[0],annual=False);storage.save_state(state,0)
        workpapers.generate(2026,1)
        old=next(w for w in workpapers.list_workpapers(2026)['items'] if w['candidate']['event']=='SELL')
        state['fvociPolicyYears']=[policy(2026,'RETAIN')];storage.save_state(state,1)
        with self.assertRaisesRegex(ValueError,'원본 데이터'):
            workpapers.approve(old['id'],'검토자','정책 변경 전 검증표 승인 차단',control_helpers.ServerControlTests.adjustments(self,old['candidate']),True)
        workpapers.generate(2026,2)
        self.assertEqual(next(w for w in workpapers.list_workpapers(2026)['items'] if w['id']==old['id'])['status'],'SUPERSEDED')
        fresh=next(w for w in workpapers.list_workpapers(2026)['items'] if w['source_revision']==2 and w['candidate']['event']=='SELL')
        self.assertEqual(fresh['candidate']['fvoci_disposal_policy'],'RETAIN')
        self.assertEqual(fresh['candidate']['oci_equity_transfer'],'0.00')

    def test_march_close_rollforward_uses_fiscal_end_year(self):
        self.database();state=state_for(CASES[0],march=True);storage.save_state(state,0)
        previous={}
        for year,reserve,oci in ((2026,-40,40),(2027,-24,56),(2028,0,32)):
            result=self.approve_close(state,year,1)
            self.assertEqual(result['opening'],previous)
            self.assertEqual(sum(map(Decimal,result['ending'].values())),Decimal(reserve))
            self.assertEqual(Decimal(calculate_year(state,year)['positions'][0]['oci']),Decimal(oci))
            previous=result['ending']

    @unittest.skipUnless(shutil.which('node'),'Node.js required')
    def test_browser_server_policy_and_candidate_agreement(self):
        inputs=[dict(state=state_for(case,march=march),year=year+int(march))
                for case in CASES for march in (False,True) for year,*_ in case['checks']]
        js="const e=require('./frontend/js/calculation.js'),fs=require('fs');process.stdout.write(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(x=>({result:e.calculate(x.state,e.fiscalEndDate(x.year,x.state.fiscalEndMonth)),rows:e.workpaperCandidates(x.state,x.year)}))));"
        browsers=json.loads(subprocess.run(['node','-e',js],input=json.dumps(inputs),cwd=ROOT,text=True,capture_output=True,check=True).stdout)
        for request,browser in zip(inputs,browsers):
            server=calculate_year(request['state'],request['year'])
            for field in ('qty','book','tax','reserve','oci','activeOCI','retainedDisposalOCI'):
                self.assertEqual(Decimal(str(browser['result']['positions'][0][field])),Decimal(str(server['positions'][0][field])))
            for row in server['ledger']:
                jsrow=next(r for r in browser['result']['ledger'] if (r['kind'],r['sourceId'])==(row['source_type'],row['source_id']))
                self.assertEqual(jsrow['fvociDisposalPolicy'],row['fvoci_disposal_policy'])
                self.assertEqual(Decimal(str(jsrow['ociEquityTransfer'])),Decimal(row['oci_equity_transfer']))
            for row in browser['rows']:
                actual=next(r for r in server['ledger'] if (r['source_type'],r['source_id'])==(row['source_type'],row['source_id']))
                self.assertEqual(row['fvoci_disposal_policy'],actual['fvoci_disposal_policy'])
