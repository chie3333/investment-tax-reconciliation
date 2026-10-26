import copy
import json
import random
import shutil
import subprocess
import unittest
from decimal import Decimal
from pathlib import Path

from app.server.calculation import calculate, money, candidates_for_year, compare_candidate, calculate_year

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = json.loads((ROOT / 'tests/fixtures/fvoci_cases.json').read_text())['cases']


class ServerCalculationTests(unittest.TestCase):
    def test_all_hand_calculated_examples_in_both_policies(self):
        for case in EXAMPLES:
            for policy in ('TRANSFER', 'RETAIN'):
                state = copy.deepcopy(case['state'])
                state['fvociPolicy'] = {'disposal': policy}
                original = copy.deepcopy(state)
                for checkpoint in case['checks']:
                    with self.subTest(case=case['id'], policy=policy, date=checkpoint['asOf']):
                        result = calculate(state, checkpoint['asOf'])
                        for expected in checkpoint['positions']:
                            actual = next(p for p in result['positions'] if p['assetId'] == expected['id'])
                            for field in ('qty','book','tax','reserve','activeOCI','oci'):
                                value = expected['retainedOCI'] if field == 'oci' and policy == 'RETAIN' else expected[field]
                                self.assertEqual(Decimal(str(actual[field])), Decimal(str(value)))
                        event = checkpoint['event']
                        row = next(r for r in result['ledger'] if r['source_type'] == event['kind'] and r['source_id'] == event['id'])
                        names = {'ociChange':'oci_change','transfer':'oci_equity_transfer','taxPL':'tax_pl',
                                 'bookPL':'book_pl','adj':'candidate_adjustment','reserveChange':'reserve_change'}
                        for field, value in event.items():
                            if field in names:
                                value = 0 if field == 'transfer' and policy == 'RETAIN' else value
                                self.assertEqual(Decimal(row[names[field]]), Decimal(str(value)))
                self.assertEqual(state, original)

    @unittest.skipUnless(shutil.which('node'), 'Node.js required for cross-language comparison')
    def test_browser_and_server_event_and_position_values_agree(self):
        inputs = [dict(state={**case['state'], 'fvociPolicy':{'disposal':policy}}, date=point['asOf'])
                  for case in EXAMPLES for policy in ('TRANSFER','RETAIN') for point in case['checks']]
        rng = random.Random(41)
        for method in ('TOTAL','MOVING'):
            for policy in ('TRANSFER','RETAIN'):
                for _ in range(12):
                    state = dict(assets=[dict(id=i,stock_code='A',classification='FVOCI' if i==1 else 'FVTPL') for i in (1,2)],
                                 trades=[],valuations=[],taxPolicy={'method':method},fvociPolicy={'disposal':policy})
                    quantities = {1:0,2:0}
                    for index in range(1,13):
                        aid=rng.choice((1,2));qty=rng.randint(1,5)
                        kind='BUY' if quantities[aid]<qty or index<3 else 'SELL'
                        quantities[aid]+=qty if kind=='BUY' else -qty
                        price=Decimal(rng.randint(1,10000))/100
                        state['trades'].append(dict(id=index,assetId=aid,date=f'2026-{index:02}-01',type=kind,qty=qty,amount=float(price*qty)))
                    inputs.append(dict(state=state,date='2026-12-31'))
        inputs.append(dict(date='2026-12-31',state=dict(assets=[dict(id=1,stock_code='A',classification='FVOCI')],
            trades=[dict(id=1,assetId=1,date='2026-01-01',type='BUY',qty=2,amount=0.03),
                    dict(id=2,assetId=1,date='2026-02-01',type='SELL',qty=1,amount=0.01)],valuations=[])))
        js = "const fs=require('fs'),e=require('./frontend/js/calculation.js');process.stdout.write(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(x=>({...e.calculate(x.state,x.date),candidates:e.workpaperCandidates(x.state,2026)}))));"
        output = subprocess.run(['node','-e',js],input=json.dumps(inputs),text=True,cwd=ROOT,capture_output=True,check=True)
        browsers = json.loads(output.stdout)
        fields = {'book_pl':'bookPL','tax_pl':'taxPL','oci_change':'ociChange','oci_equity_transfer':'ociEquityTransfer',
                  'candidate_adjustment':'adj','reserve_change':'reserveChange','tax_basis':'tax','book_basis':'book'}
        for request, browser in zip(inputs, browsers):
            server = calculate(request['state'],request['date'])
            canonical=candidates_for_year(calculate_year(request['state'],2026),2026,12)
            self.assertEqual(len(canonical),len(browser['candidates']))
            for source,expected in zip(browser['candidates'],canonical):
                compare_candidate(source,expected)
            for a,b in zip(server['positions'],browser['positions']):
                for field in ('qty','book','tax','reserve','oci','activeOCI','retainedDisposalOCI','pl','dividend'):
                    self.assertEqual(Decimal(str(a[field])),Decimal(str(b[field])))
            self.assertEqual(len(server['ledger']),len(browser['ledger']))
            for a,b in zip(server['ledger'],browser['ledger']):
                self.assertEqual(a['source_id'],b['sourceId'])
                for sf,bf in fields.items():
                    self.assertEqual(Decimal(a[sf]),Decimal(str(b[bf])),f'{request}: {a["source_id"]} {sf}')
                self.assertEqual(a['allocation_effects'],[
                    {k:(money(v) if k!='assetId' else v) for k,v in effect.items()} for effect in b['allocationEffects']])

    def test_rounding_and_residual_never_allocate_to_empty_asset(self):
        self.assertEqual(money('1.005'),'1.01')
        self.assertEqual(money('-1.005'),'-1.01')
        state = {'assets':[dict(id=i,stock_code='A',classification='FVOCI') for i in range(1,5)],
                 'trades':[dict(id=i,assetId=i,date='2026-01-01',type='BUY',qty=1,amount=a) for i,a in enumerate((100,100,101),1)],'valuations':[]}
        self.assertEqual([p['tax'] for p in calculate(state,'2026-12-31')['positions']],['100.33','100.33','100.34','0.00'])

    def test_invalid_data_never_silently_drops_an_event(self):
        state = copy.deepcopy(EXAMPLES[0]['state'])
        for value in ('NaN', 'Infinity', -1):
            with self.subTest(amount=value):
                bad = copy.deepcopy(state);bad['trades'][0]['amount']=value
                with self.assertRaises(ValueError):calculate(bad,'2026-12-31')
        bad = copy.deepcopy(state);bad['trades'][1]['qty']=99
        with self.assertRaisesRegex(ValueError,'초과'):calculate(bad,'2026-12-31')
        bad = copy.deepcopy(state);bad['trades'].append(dict(bad['trades'][0]))
        with self.assertRaisesRegex(ValueError,'중복'):calculate(bad,'2026-12-31')

    def test_fiscal_policy_change_and_specific_lots(self):
        state={'fiscalEndMonth':3,'assets':[dict(id=1,stock_code='A',classification='FVTPL')],
               'taxPolicyYears':[{'effectiveYear':2026,'method':'TOTAL'},{'effectiveYear':2027,'method':'MOVING'}],
               'trades':[dict(id=1,assetId=1,date='2026-03-01',type='BUY',qty=10,amount=100),
                         dict(id=2,assetId=1,date='2026-04-01',type='SELL',qty=5,amount=90),
                         dict(id=3,assetId=1,date='2026-09-01',type='BUY',qty=10,amount=300)],'valuations':[]}
        self.assertEqual(calculate(state,'2027-03-31')['ledger'][1]['tax_pl'],'40.00')
        state['stockPolicies']={'A':{'book':'SPECIFIC'}}
        with self.assertRaisesRegex(ValueError,'LOT'):calculate(state,'2027-03-31')
        state['trades'][1]['lotAllocation']=[{'id':1,'qty':5}]
        self.assertEqual(calculate(state,'2027-03-31')['ledger'][1]['tax_pl'],'40.00')
        state['trades'][1]['lotAllocation']=[{'id':1,'qty':4},{'id':1,'qty':4}]
        with self.assertRaisesRegex(ValueError,'LOT'):calculate(state,'2027-03-31')
