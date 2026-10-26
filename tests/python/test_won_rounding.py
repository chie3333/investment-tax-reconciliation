"""Fixed whole-won answers, cross-language replay, and real approval/closing APIs."""
import copy
import json
import random
import subprocess
import tempfile
import unittest
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from app.server import storage,workpapers,closing
from app.server.calculation import calculate,calculate_year,compare_candidate,candidates_for_year
from app.server.reconciliation import run_validation
from app.server.rounding import whole,WON
from app.server.validation_engine import validate
from app.database.bootstrap import create_database
from tests.python import test_tax_adjustment_rules as rules
from tests.python import test_database_app as normalized
from tests.python import test_fvoci_policy_years as years

ROOT=Path(__file__).resolve().parents[2]
ANSWERS=json.loads((ROOT/'tests/fixtures/won_rounding.json').read_text())
POLICY=dict(profile=WON,effectiveYear=2026)


def state_for(case,method='MOVING',classification='FVTPL'):
    return dict(roundingPolicy=POLICY,master=[],assets=[dict(id=1,stock_code='A',classification=classification)],
                trades=[dict(id=1,assetId=1,date='2026-01-01',type='BUY',qty=case['qty'],amount=case['cost'],price=case['cost']/case['qty'])]+
                [dict(id=i+2,assetId=1,date=f'2026-02-{i+1:02}',type='SELL',qty=1,amount=case['proceeds'],price=case['proceeds']) for i in range(case['qty'])],
                valuations=[],adjustments=[],taxPolicyYears=[],taxPolicy=dict(method=method))


def browsers(requests):
    script="const e=require('./frontend/js/calculation.js'),fs=require('fs');process.stdout.write(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(x=>e.calculate(x.state,x.date))));"
    return json.loads(subprocess.run(['node','-e',script],cwd=ROOT,input=json.dumps(requests),text=True,capture_output=True,check=True).stdout)


class WholeWonTests(unittest.TestCase):
    def equal_browser(self,state,as_of,client):
        result=calculate(state,as_of)
        self.assertFalse(any(r.get('error') for r in client['ledger']),client)
        self.assertEqual(len(result['ledger']),len(client['ledger']))
        for p,b in zip(result['positions'],client['positions']):
            for k in ('qty','book','tax','reserve','oci','activeOCI','retainedDisposalOCI','pl','dividend'):
                self.assertEqual(Decimal(str(p[k])),Decimal(str(b[k])),(k,state,as_of))
        for p,b in zip(result['ledger'],client['ledger']):
            for left,right in {'book_pl':'bookPL','tax_pl':'taxPL','oci_change':'ociChange','oci_equity_transfer':'ociEquityTransfer','candidate_adjustment':'adj','reserve_change':'reserveChange','tax_basis':'tax','book_basis':'book'}.items():
                self.assertEqual(Decimal(str(p[left])),Decimal(str(b[right])),(left,state,as_of,p['source_id']))
            self.assertEqual(len(p['allocation_effects']),len(b['allocationEffects']))
            for a,e in zip(p['allocation_effects'],b['allocationEffects']):
                self.assertEqual(a['assetId'],e['assetId'])
                for key in ('book','tax','reserve','reserveChange'):self.assertEqual(Decimal(a[key]),Decimal(str(e[key])))
        return result

    def test_half_ties_and_fixed_disposal_answers(self):
        for value,expected in ANSWERS['rounding']:self.assertEqual(whole(Fraction(value)),expected)
        for case in ANSWERS['disposals']:
            for method in ('TOTAL','MOVING'):
                state=state_for(case,method)
                for stop in range(case['qty']):
                    day=f'2026-02-{stop+1:02}'
                    result=self.equal_browser(state,day,browsers([dict(state=state,date=day)])[0])
                    sales=[r for r in result['ledger'] if r['event']=='SELL']
                    self.assertEqual([case['proceeds']-int(r['tax_pl']) for r in sales],case['costs'][:stop+1])
                    self.assertEqual([int(r['tax_basis']) for r in sales],case['remaining'][:stop+1])
                    self.assertEqual([case['proceeds']-int(r['book_pl']) for r in sales],case['costs'][:stop+1])
                    self.assertEqual(result['positions'][0]['reserve'],'0')
        # Moving-average stock cycles close at zero even if a later buy exists.
        state=state_for(ANSWERS['disposals'][0])
        state['trades'] += [dict(id=5,assetId=1,date='2026-03-01',type='BUY',qty=2,amount=21),
                            dict(id=6,assetId=1,date='2026-04-01',type='SELL',qty=1,amount=20),
                            dict(id=7,assetId=1,date='2026-04-02',type='SELL',qty=1,amount=20)]
        result=self.equal_browser(state,'2026-12-31',browsers([dict(state=state,date='2026-12-31')])[0])
        self.assertEqual([int(t['amount'])-int(r['tax_pl']) for t in state['trades'] if t['type']=='SELL'
                          for r in result['ledger'] if r['source_type']=='trade' and r['source_id']==t['id']], [33,33,34,11,10])

    def test_aggregate_valuation_and_fvoci_transfer_retain(self):
        state=state_for(dict(qty=3,cost=3,proceeds=1));state['trades']=state['trades'][:1]
        state['valuations']=[dict(id=1,assetId=1,date='2026-12-31',qty=3,price='0.49',fairValue='1.47')]
        result=self.equal_browser(state,'2026-12-31',browsers([dict(state=state,date='2026-12-31')])[0])
        self.assertEqual(result['positions'][0]['book'],'1');self.assertEqual(result['positions'][0]['pl'],'-2')
        for policy in ('TRANSFER','RETAIN'):
            state=state_for(dict(qty=3,cost=100,proceeds=50),classification='FVOCI');state['fvociPolicy']={'disposal':policy}
            state['valuations']=[dict(id=1,assetId=1,date='2026-01-31',price=40,qty=3,fairValue=120)]
            result=self.equal_browser(state,'2026-12-31',browsers([dict(state=state,date='2026-12-31')])[0])
            self.assertEqual(result['positions'][0]['oci'],'0' if policy=='TRANSFER' else '50')
            self.assertEqual(sum(Decimal(r['tax_pl']) for r in result['ledger']),50)
            self.assertEqual(sum(Decimal(r['oci_equity_transfer']) for r in result['ledger']),50 if policy=='TRANSFER' else 0)

    def test_large_amount_regression_and_200_adversarial_cross_language_paths(self):
        requests=[]
        for profile in (None,POLICY):
            state=state_for(ANSWERS['disposals'][-1]);state['trades']=state['trades'][:1]
            if profile is None:state.pop('roundingPolicy')
            requests.append(dict(state=state,date='2026-12-31'))
        rng=random.Random(2605)
        for i in range(200):
            s=dict(roundingPolicy=POLICY,assets=[dict(id=j,stock_code='A',classification='FVOCI' if j==1 else 'FVTPL') for j in (1,2)],trades=[],valuations=[],
                   fvociPolicy=dict(disposal=('TRANSFER','RETAIN')[i%2]),taxPolicy=dict(method=('TOTAL','MOVING')[(i//2)%2]))
            held={1:0,2:0}
            for j in range(1,17):
                aid=rng.choice((1,2));qty=rng.randint(1,6);kind='BUY' if held[aid]<qty or j<3 else 'SELL';held[aid]+=qty if kind=='BUY' else -qty
                # Include exact half-won/near-half inputs and 10-trillion-won amounts.
                amount=str(Decimal(rng.choice((rng.randint(0,200),10000000000000)))+Decimal(rng.choice(('0','0.49','0.5','0.50000000000001'))))
                s['trades'].append(dict(id=j,assetId=aid,date=f'2026-03-{j:02}',type=kind,qty=qty,amount=amount))
            requests.append(dict(state=s,date='2026-12-31'))
        for request,client in zip(requests,browsers(requests)):
            result=self.equal_browser(request['state'],request['date'],client)
            for p in result['positions']:
                self.assertEqual(Decimal(p['tax'])-Decimal(p['book']),Decimal(p['reserve']))
            self.assertEqual(sum(Decimal(p['tax']) for p in result['positions']),Decimal(result['stockPools'][0]['tax']))

    def test_many_assets_tiny_cost_does_not_allocate_negative_residual(self):
        state=dict(roundingPolicy=POLICY,assets=[dict(id=i,stock_code='A',classification='FVOCI' if i%2 else 'FVTPL') for i in range(1,5)],
                   trades=[dict(id=i,assetId=i,date='2026-01-01',type='BUY',qty=1,amount=2 if i==1 else 0) for i in range(1,5)],valuations=[])
        result=self.equal_browser(state,'2026-12-31',browsers([dict(state=state,date='2026-12-31')])[0])
        self.assertEqual([p['tax'] for p in result['positions']],['1','1','0','0'])

    def test_existing_cent_history_and_fractional_opening_are_not_rewritten(self):
        s=years.state_for(years.CASES[0],annual=False)
        old=calculate_year(s,2025);s['roundingPolicy']=POLICY
        self.assertEqual(calculate_year(s,2025),old)
        self.equal_browser(s,'2027-12-31',browsers([dict(state=s,date='2027-12-31')])[0])
        s=state_for(ANSWERS['disposals'][0]);s['trades'][0]['date']='2025-01-01'
        s['trades'][1]['date']='2025-02-01'
        with self.assertRaisesRegex(ValueError,'전기 소수 잔액'):calculate_year(s,2026)
        self.assertTrue(browsers([dict(state=s,date='2026-12-31')])[0]['ledger'][0]['error'])

    def test_march_fiscal_policy_change_and_exact_large_fraction_inputs(self):
        requests=[]
        s=years.state_for(years.CASES[0],march=True);s['roundingPolicy']=POLICY
        s['taxPolicyYears']=[dict(effectiveYear=2027,method='TOTAL'),dict(effectiveYear=2028,method='MOVING')]
        for year in (2026,2027,2028):requests.append(dict(state=s,date=f'{year}-03-31'))
        # Amount text is parsed before arithmetic, including sub-binary-ulp ties.
        for amount in ('10000000000000.499999999999999999','10000000000000.5'):
            s=state_for(ANSWERS['disposals'][0]);s['trades']=s['trades'][:1];s['trades'][0]['amount']=amount
            requests.append(dict(state=s,date='2026-12-31'))
        for request,client in zip(requests,browsers(requests)):self.equal_browser(request['state'],request['date'],client)


class WholeWonApiTests(unittest.TestCase):
    setUp=normalized.NormalizedAppTests.setUp
    request=normalized.NormalizedAppTests.request
    post=normalized.NormalizedAppTests.post
    save=normalized.NormalizedAppTests.save
    approve_close=normalized.NormalizedAppTests.approve_close
    def test_whole_won_approval_cancel_reapprove_close_and_profile(self):
        s=rules.state_for(rules.CASES[2]);s['roundingPolicy']=POLICY
        # A non-divisible cost and fractional quote exercise rounding in the API.
        s['trades'][0].update(amount=101,price=10.1)
        self.save(s)
        self.post('/api/workpapers/generate',year=2026,expected_revision=1)
        for wp in workpapers.list_workpapers(2026)['items']:
            self.assertEqual(wp['candidate']['adjustment_plan']['status'],'REVIEW_REQUIRED')
            for row in wp['candidate']['adjustment_plan']['rows']:
                self.assertEqual(Decimal(row['amount']),Decimal(row['amount']).to_integral_value())
        wp=workpapers.list_workpapers(2026)['items'][0]
        rows=wp['candidate']['adjustment_plan']['rows']
        self.post('/api/workpapers/approve',id=wp['id'],reviewer='검토자',note='원 단위 정답과 원장 금액 검토',adjustments=rows,acknowledge=True)
        self.post('/api/workpapers/cancel',id=wp['id'],reviewer='검토자',reason='원 단위 역분개 원본 대사 확인')
        closed=self.approve_close(s,2026)
        with storage.connect() as c:
            self.assertEqual(c.execute('SELECT DISTINCT rounding_profile_id FROM calculation_runs').fetchone()[0],WON)
            frozen=[dict(r) for r in c.execute('SELECT * FROM fiscal_closes')]
        changed=copy.deepcopy(s);changed['roundingPolicy']['effectiveYear']=2027
        self.assertEqual(self.request('/api/state',dict(state=changed,expected_revision=1))[0],400)
        with storage.connect() as c:self.assertEqual([dict(r) for r in c.execute('SELECT * FROM fiscal_closes')],frozen)

    def test_legacy_close_and_future_won_policy_carryforward(self):
        s=years.state_for(years.CASES[0],annual=False);self.save(s)
        prior=self.approve_close(s,2025)
        with storage.connect() as c:frozen=[dict(r) for r in c.execute('SELECT * FROM fiscal_closes')]
        s['roundingPolicy']=POLICY;self.save(s,1)
        closed=self.approve_close(s,2026,2)
        self.assertEqual(closed['opening'],prior['ending'])
        with storage.connect() as c:self.assertEqual(dict(c.execute('SELECT * FROM fiscal_closes ORDER BY close_id LIMIT 1').fetchone()),frozen[0])

    def test_invalid_policy_stale_workpaper_and_fractional_manual_rows(self):
        s=rules.state_for(rules.CASES[2]);self.save(s)
        self.post('/api/workpapers/generate',year=2026,expected_revision=1)
        old=workpapers.list_workpapers(2026)['items'][0]
        s['roundingPolicy']=POLICY;self.save(s,1)
        self.assertEqual(self.request('/api/workpapers/approve',dict(id=old['id'],reviewer='검토자',note='옛 정책 승인 금액 차단 검토',adjustments=old['candidate']['adjustment_plan']['rows'],acknowledge=True))[0],400)
        for policy in ({'profile':WON,'effectiveYear':True},{'profile':'OTHER','effectiveYear':2026}):
            bad=copy.deepcopy(s);bad['roundingPolicy']=policy
            self.assertEqual(self.request('/api/state',dict(state=bad,expected_revision=2))[0],400)
        self.assertEqual(storage.read_state()['revision'],2)
        self.post('/api/workpapers/generate',year=2026,expected_revision=2)
        wp=next(w for w in workpapers.list_workpapers(2026)['items'] if w['status']=='DRAFT')
        rows=copy.deepcopy(wp['candidate']['adjustment_plan']['rows'])
        first=rows.pop(0);rows+=[{**first,'amount':str(Decimal(first['amount'])-Decimal('0.5'))},{**first,'amount':'0.5'}]
        code,error=self.request('/api/workpapers/approve',dict(id=wp['id'],reviewer='검토자',note='반원 조정행 분할을 차단하는 검토',adjustments=rows,acknowledge=True))
        self.assertEqual(code,400);self.assertIn('정수 원',error['error'])
        self.assertEqual(closing.summary(2026)['entries'],[])

    def test_one_won_and_fractional_mismatch_are_not_tolerated(self):
        s=state_for(ANSWERS['disposals'][0]);result=calculate_year(s,2026)
        for delta in ('1','0.1'):
            wrong=copy.deepcopy(result);wrong['positions'][0]['reserve']=delta
            codes={i['code'] for i in validate(s,2026,wrong)['issues']}
            self.assertIn('REC_RESERVE',codes)

    def test_march_whole_won_closing_carryforward(self):
        s=years.state_for(years.CASES[0],march=True);s['roundingPolicy']=POLICY;self.save(s)
        previous={}
        for year in (2026,2027,2028):
            closed=self.approve_close(s,year)
            self.assertEqual(closed['opening'],previous)
            previous=closed['ending']
