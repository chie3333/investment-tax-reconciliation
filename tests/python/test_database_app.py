"""Existing HTTP contract backed entirely by normalized SQL tables."""
import copy
import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from app.database.bootstrap import create_database, inspect_database
from app.server import storage, workpapers, closing
from app.server.calculation import calculate_year
from tests.python import test_server_api as api
from tests.python import test_server_controls as controls
from tests.python import test_tax_adjustment_rules as rules
from tests.python import test_fvoci_policy_years as annual


class NormalizedAppTests(unittest.TestCase):
    request=api.ServerApiTests.request

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'app.sqlite3'
        self.old=storage.DB_PATH;storage.DB_PATH=self.path
        self.addCleanup(setattr,storage,'DB_PATH',self.old)
        create_database(self.path)

    def post(self,path,**payload):
        code,result=self.request(path,payload)
        self.assertEqual(code,200,result)
        return result

    def save(self,state,revision=0):
        self.post('/api/state',state=state,expected_revision=revision)
        code,result=self.request('/api/state');self.assertEqual(code,200)
        self.assertEqual(result['state'],state)
        self.assertEqual(calculate_year(result['state'],2026),calculate_year(state,2026))

    def approve_close(self,state,year,revision=1):
        self.post('/api/workpapers/generate',year=year,expected_revision=revision)
        papers=workpapers.list_workpapers(year)['items']
        for wp in papers:
            if wp['status']!='DRAFT':continue
            rows=wp['candidate']['adjustment_plan']['rows']
            if wp['candidate']['adjustment_plan']['status']=='MANUAL_REQUIRED':
                rows=controls.ServerControlTests.adjustments(self,wp['candidate'])
            self.post('/api/workpapers/approve',id=wp['id'],reviewer='검토자',note='계산 및 법령 요건 독립 검토 확인',adjustments=rows,acknowledge=True)
        calculated=calculate_year(state,year)
        result=self.post('/api/validate',year=year,expected_revision=revision,calculated={'positions':calculated['positions']})
        self.assertEqual(result['summary']['ERROR'],0,result['issues'])
        closed=self.post('/api/closing/close',year=year,reviewer='검토자',note='원장과 기말 잔액 대사 완료',acknowledge_warnings=True,positions=calculated['positions'])
        self.assertTrue(closed['close'])
        self.assertEqual(sum(map(Decimal,closed['ending'].values())),sum(Decimal(p['reserve']) for p in calculated['positions']))
        return closed

    def test_all_answer_cases_through_http_and_normalized_approval_close(self):
        for index,case in enumerate(rules.CASES):
            for policy in ('TRANSFER','RETAIN'):
                with self.subTest(case=case['id'],policy=policy):
                    storage.DB_PATH=Path(self.tmp.name)/f'{index}-{policy}.sqlite3';create_database(storage.DB_PATH)
                    state=rules.state_for(case,policy);self.save(state)
                    self.approve_close(state,2026)
                    with storage.connect() as c:
                        self.assertFalse(c.execute("SELECT 1 FROM sqlite_master WHERE name='workspace'").fetchone())
                        self.assertEqual(c.execute('SELECT count(*) FROM trade_events').fetchone()[0],len(state['trades']))
                        self.assertGreater(c.execute('SELECT count(*) FROM calculated_balances').fetchone()[0],0)
                        self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
                        self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                        self.assertEqual(c.execute('SELECT status FROM fiscal_years WHERE fiscal_year=2026').fetchone()[0],'CLOSED')
                        decisions=c.execute("SELECT approved_rows_json FROM workpaper_decisions WHERE action='APPROVE'").fetchall()
                        self.assertEqual(sum(len(json.loads(d[0])) for d in decisions),c.execute('SELECT count(*) FROM tax_adjustment_rows').fetchone()[0])

    def test_versioned_sources_conflicts_rollback_and_cancellation(self):
        state=rules.state_for(rules.CASES[2]);self.save(state)
        self.post('/api/workpapers/generate',year=2026,expected_revision=1)
        wp=workpapers.list_workpapers(2026)['items'][0]
        rows=wp['candidate']['adjustment_plan']['rows']
        self.post('/api/workpapers/approve',id=wp['id'],reviewer='검토자',note='정정 전 최초 조정행 검토 완료',adjustments=rows,acknowledge=True)
        self.post('/api/workpapers/cancel',id=wp['id'],reviewer='검토자',reason='원본 승인 근거 보존 후 취소 검토')
        self.assertTrue(all(Decimal(v)==0 for v in closing.summary(2026)['ending'].values()))
        self.post('/api/workpapers/generate',year=2026,expected_revision=1)
        self.approve_close(state,2026)
        changed=copy.deepcopy(state);changed['trades'][0]['amount']=101
        code,error=self.request('/api/state',dict(state=changed,expected_revision=1));self.assertEqual(code,400,error)
        self.assertEqual(storage.read_state()['state'],state)
        with storage.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM workpaper_decisions WHERE action='CANCEL'").fetchone()[0],1)
            self.assertGreater(c.execute("SELECT count(*) FROM reserve_entries WHERE entry_type='REVERSAL'").fetchone()[0],0)
            with self.assertRaises(sqlite3.IntegrityError):c.execute('DELETE FROM fiscal_closes')

    def test_open_source_revisions_and_invalid_save_is_atomic(self):
        state=rules.state_for(rules.CASES[2]);self.save(state)
        changed=copy.deepcopy(state);changed['trades'][0]['amount']=110;self.save(changed,1)
        with storage.connect() as c:
            rows=c.execute("SELECT version,is_current,gross_amount FROM trade_events WHERE logical_key='trade:1' ORDER BY version").fetchall()
            self.assertEqual([tuple(r) for r in rows],[(1,0,'100'),(2,1,'110')])
        self.assertEqual(self.request('/api/state',dict(state=changed,expected_revision=1))[0],409)
        bad=copy.deepcopy(changed);bad['trades'][0]['date']='2026-02-30'
        self.assertEqual(self.request('/api/state',dict(state=bad,expected_revision=2))[0],400)
        self.assertEqual(storage.read_state()['revision'],2)
        self.assertEqual(storage.read_state()['state'],changed)
        self.assertTrue(storage.read_audit())

    def test_annual_policies_march_carryforward_and_frozen_history(self):
        for march in (False,True):
            storage.DB_PATH=Path(self.tmp.name)/f'annual-{march}.sqlite3';create_database(storage.DB_PATH)
            state=annual.state_for(annual.CASES[0],annual=False,march=march);self.save(state)
            first=2025+int(march);previous=self.approve_close(state,first)
            with storage.connect() as c:frozen=[dict(r) for r in c.execute('SELECT * FROM fiscal_closes')]
            state['fvociPolicyYears']=[annual.policy(first+1,'RETAIN')];self.save(state,1)
            closed=self.approve_close(state,first+1,2)
            self.assertEqual(closed['opening'],previous['ending'])
            with storage.connect() as c:self.assertEqual(dict(c.execute('SELECT * FROM fiscal_closes ORDER BY close_id LIMIT 1').fetchone()),frozen[0])
            changed=copy.deepcopy(state);changed['fvociPolicyYears'][0]['disposal']='TRANSFER'
            self.assertEqual(self.request('/api/state',dict(state=changed,expected_revision=2))[0],400)

    def test_validation_history_and_issue_review_routes(self):
        state=rules.state_for(rules.CASES[2]);self.save(state)
        result=self.post('/api/validate',year=2026,expected_revision=1,calculated={'positions':[]})
        issue=next(i for i in result['issues'] if i['code']=='SERVER_CALC_MISMATCH')
        key=f"{issue['code']}|{issue.get('ref','')}"
        self.post('/api/validation/review',issue_key=key,status='REVIEWING',reason='브라우저 결과 확인 중',run_id=result['run_id'])
        self.assertEqual(storage.read_issue_reviews()['reviews'][0]['status'],'REVIEWING')
        self.assertEqual(json.loads(storage.read_validation_runs()[0]['result_json']),result)
        self.assertEqual(len(storage.read_issue_reviews()['events']),1)

    def test_browser_defaults_metadata_order_dividend_and_void_roundtrip(self):
        state=rules.state_for(rules.CASES[2]);state.update(fiscalEndMonth=12,stockPolicies={'005930':{'book':'MOVING'}},taxBasis={'1':{'amount':100}},ids={'asset':2,'trade':3})
        state['master']=[dict(code='005930',name='삼성전자')]
        state['assets'].insert(0,dict(id=2,stock_code='005930',asset_name='동일 종목 손익분류',classification='FVTPL'))
        state['trades'].insert(0,dict(id=3,assetId=2,date='2026-07-02',type='DIVIDEND',qty=0,amount=10,price=0,note='배당 확인'))
        state['taxPolicy']=dict(method='TOTAL',filingStatus='UNKNOWN',effectiveYear='',filingDate='',receipt='',changeStatus='NO',previousMethod='',changeDate='',evidence='')
        state['fvociPolicyYears']=[annual.policy(2028,'TRANSFER'),annual.policy(2027,'RETAIN')]
        state['adjustments']=[dict(id=1,assetId=1,date='2026-08-01',amount=10,note='기존 수동 조정 참고')]
        self.save(state)
        changed=copy.deepcopy(state);changed['trades']=[r for r in changed['trades'] if r['id']!=3];changed['adjustments']=[]
        self.save(changed,1)
        with storage.connect() as c:
            self.assertEqual(c.execute("SELECT status FROM trade_events WHERE logical_key='trade:3' AND is_current=1").fetchone()[0],'VOID')
            self.assertEqual(c.execute('SELECT count(*) FROM reserve_entries').fetchone()[0],0)
        for value in ('invalid',True):
            bad=copy.deepcopy(changed);bad['trades'][0]['amount']=value
            self.assertEqual(self.request('/api/state',dict(state=bad,expected_revision=2))[0],400)
        bad=copy.deepcopy(changed);bad['trades'].append(bad['trades'][0])
        self.assertEqual(self.request('/api/state',dict(state=bad,expected_revision=2))[0],400)
        self.assertEqual(storage.read_state()['state'],changed)

    def test_startup_creates_normalized_file_and_preserves_legacy_file(self):
        from app.server import server
        storage.DB_PATH=Path(self.tmp.name)/'fresh.sqlite3'
        with patch.object(server,'ThreadingHTTPServer'),patch('builtins.print'):server.main()
        self.assertTrue(storage.using_normalized())
        self.assertIsNone(storage.read_state()['state'])
        storage.DB_PATH=Path(self.tmp.name)/'legacy.sqlite3'
        state=rules.state_for(rules.CASES[2]);storage.save_state(state,0)
        with patch.object(server,'ThreadingHTTPServer'),patch('builtins.print'):server.main()
        self.assertFalse(storage.using_normalized())
        self.assertEqual(storage.read_state()['state'],state)
