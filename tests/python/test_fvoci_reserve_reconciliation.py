import copy
import json
import tempfile
import unittest
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from app.server import storage, workpapers, closing
from app.server.calculation import calculate
from app.server.reconciliation import run_validation
from validation.reserve_reference import audit, cents

CASES = json.loads((Path(__file__).parents[1] / 'fixtures/fvoci_reserve_cases.json').read_text())['cases']


def amount(value):
    if isinstance(value, Fraction):
        return format(Decimal(value.numerator) / Decimal(value.denominator), '.2f')
    return str(value)


def state_for(case, policy='TRANSFER'):
    state = dict(master=[], assets=[dict(id=i, stock_code='A', asset_name='검산 종목', classification=c)
                                    for i,c in case['assets']], trades=[], valuations=[], adjustments=[],
                 taxPolicy={'method':case['method']}, taxPolicyYears=[], fvociPolicy={'disposal':policy})
    for i,a,d,t,q,v in case['trades']:
        state['trades'].append(dict(id=i,assetId=a,date=d,type=t,qty=q,amount=v,price=str(Decimal(str(v))/q)))
    for i,a,d,p in case['valuations']:
        qty = sum(t['qty'] * (1 if t['type']=='BUY' else -1) for t in state['trades'] if t['assetId']==a and t['date']<=d)
        state['valuations'].append(dict(id=i,assetId=a,date=d,price=p,qty=qty,fairValue=str(Decimal(str(p))*qty)))
    return state


def adjustment_rows(event):
    """Approval inputs come from the independent oracle, never the candidate."""
    rows = [dict(asset_id=i, direction='익금산입' if movement>0 else '손금산입',
                 disposition='유보' if movement>0 else '△유보', amount=amount(abs(movement)),
                 reserve_movement=amount(movement), legal_basis='산술 정답표 검산용; 법정 소득처분 확정 아님')
            for i,movement in event['movements'].items() if movement]
    other = event['tax_pl'] - event['book_pl'] - sum(event['movements'].values())
    if other:
        rows.append(dict(direction='익금산입' if other>0 else '손금산입', disposition='기타',
                         amount=amount(abs(other)), reserve_movement='0', legal_basis='산술 연결 테스트용 가정'))
    return rows


class FvociReserveReconciliationTests(unittest.TestCase):
    def isolated_database(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        old = storage.DB_PATH
        storage.DB_PATH = Path(temporary.name) / 'reserve.sqlite3'
        self.addCleanup(setattr, storage, 'DB_PATH', old)

    def test_manual_sale_checkpoints_and_every_server_event(self):
        for case in CASES:
            for policy in ('TRANSFER', 'RETAIN'):
                with self.subTest(case=case['id'], policy=policy):
                    state = state_for(case, policy)
                    original = copy.deepcopy(state)
                    end = max(t['date'] for t in state['trades'] + state['valuations'])[:4]+'-12-31'
                    reference = audit(state, end)
                    for eid,removed,cost,movement,balance in case['sales']:
                        e = next(e for e in reference['events'] if e['source_type']=='trade' and e['source_id']==eid)
                        self.assertEqual([e['removed_book'],e['tax_cost'],e['movements'][e['asset_id']],e['after'][e['asset_id']]['reserve']],
                                         [Fraction(str(v)) for v in (removed,cost,movement,balance)])
                    for aid,qty,book,tax,reserve in case['ending']:
                        self.assertEqual(reference['positions'][aid], dict(qty=qty,book=Fraction(str(book)),tax=Fraction(str(tax)),reserve=Fraction(str(reserve))))
                    # Every intermediate date, not just final net totals.
                    for expected in reference['events']:
                        actual = calculate(state, expected['date'])
                        row = next(r for r in actual['ledger'] if (r['source_type'],r['source_id']) == (expected['source_type'],expected['source_id']))
                        for field in ('tax_pl','book_pl'):
                            self.assertEqual(Decimal(row[field]),Decimal(amount(expected[field])))
                        self.assertEqual(Decimal(row['reserve_change']),Decimal(amount(expected['movements'][expected['asset_id']])))
                        effects = {e['assetId']:e for e in row['allocation_effects']}
                        for aid, movement in expected['movements'].items():
                            self.assertEqual(Decimal(effects.get(aid,{}).get('reserveChange','0')), Decimal(amount(movement)))
                        for position in actual['positions']:
                            for field in ('qty','book','tax','reserve'):
                                value = expected['after'][position['assetId']][field]
                                self.assertEqual(Decimal(str(position[field])),Decimal(str(value) if field=='qty' else amount(value)))
                        self.assertEqual(sum(Decimal(p['tax']) for p in actual['positions']),Decimal(amount(expected['pool_tax'])))
                    self.assertEqual(state, original)

    def test_independent_amounts_approve_post_and_close_every_year(self):
        for case in CASES:
            for policy in ('TRANSFER','RETAIN'):
                with self.subTest(case=case['id'], policy=policy):
                    self.isolated_database()
                    state = state_for(case, policy); storage.save_state(state,0)
                    first = min(t['date'] for t in state['trades'])[:4]
                    last = max(t['date'] for t in state['trades']+state['valuations'])[:4]
                    previous_ending = {}
                    for year in range(int(first),int(last)+1):
                        reference = audit(state,f'{year}-12-31')
                        expected_events = {(e['source_type'],e['source_id']):e for e in reference['events'] if e['date'].startswith(str(year))}
                        workpapers.generate(year,1)
                        papers = workpapers.list_workpapers(year)['items']
                        required = {key for key,e in expected_events.items() if e['event'] in ('SELL','VALUATION') or any(e['movements'].values())}
                        self.assertEqual({(w['candidate']['source_type'],w['candidate']['source_id']) for w in papers},required)
                        for wp in papers:
                            candidate = wp['candidate']; e = expected_events[(candidate['source_type'],candidate['source_id'])]
                            workpapers.approve(wp['id'],'검산 담당자','독립 산술 정답과 자산별 증감 확인',adjustment_rows(e),True)
                            posted = [p for p in closing.summary(year)['entries'] if p['source_ref'].startswith(f"WORKPAPER:{wp['id']}:")]
                            for aid,movement in e['movements'].items():
                                self.assertEqual(sum((Decimal(p['amount']) for p in posted if int(p['asset_id'])==aid),Decimal(0)),Decimal(amount(movement)))
                        positions = [dict(assetId=i,**{k:amount(v) if k!='qty' else v for k,v in p.items()}) for i,p in reference['positions'].items()]
                        validation = run_validation(year,1,{'positions':positions})
                        self.assertEqual(validation['summary']['ERROR'],0,validation['issues'])
                        result = closing.close_year(year,'검산 담당자','독립 정답표와 원장·기말잔액 대사',True,positions)
                        self.assertIsNotNone(result['close'])
                        self.assertEqual(result['opening'],previous_ending)
                        for aid,p in reference['positions'].items():
                            balance = sum((Decimal(v) for k,v in result['ending'].items() if k.split('|')[0]==str(aid)),Decimal(0))
                            self.assertEqual(balance,Decimal(amount(p['reserve'])))
                        previous_ending = result['ending']

    def test_wrong_partial_release_blocks_approval_without_posting(self):
        self.isolated_database()
        state = state_for(CASES[0]); storage.save_state(state,0)
        reference = audit(state,'2026-12-31'); workpapers.generate(2026,1)
        papers = workpapers.list_workpapers(2026)['items']
        sale = next(w for w in papers if w['candidate']['event']=='SELL')
        event = next(e for e in reference['events'] if e['event']=='SELL')
        for wrong in ('0','40','-20','20.01'):
            rows = adjustment_rows(event)
            rows[0].update(reserve_movement=wrong,amount=str(abs(Decimal(wrong))),
                           direction='익금산입' if Decimal(wrong)>0 else '손금산입',
                           disposition='유보' if Decimal(wrong)>0 else '△유보')
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                workpapers.approve(sale['id'],'검산 담당자','잘못된 환입의 차단 테스트',rows,True)
            self.assertEqual(closing.summary(2026)['entries'],[])
        run_validation(2026,1)
        with self.assertRaisesRegex(ValueError,'미승인'):
            closing.close_year(2026,'검산 담당자','승인 누락 차단',True,calculate(state,'2026-12-31')['positions'])

    def test_rounding_and_reference_scope(self):
        self.assertEqual(cents(Fraction(1005,1000)),Fraction(101,100))
        self.assertEqual(cents(Fraction(-1005,1000)),Fraction(-101,100))
        state = state_for(CASES[0]); state['taxPolicyYears']=[{'effectiveYear':2026,'method':'TOTAL'}]
        with self.assertRaises(ValueError): audit(state,'2026-12-31')

    def test_equal_ending_total_does_not_hide_wrong_event_postings(self):
        self.isolated_database()
        state = state_for(CASES[0]); storage.save_state(state,0)
        reference = audit(state,'2026-12-31'); workpapers.generate(2026,1)
        papers = workpapers.list_workpapers(2026)['items']
        for wp in papers:
            candidate = wp['candidate']
            event = next(e for e in reference['events'] if (e['source_type'],e['source_id'])==(candidate['source_type'],candidate['source_id']))
            workpapers.approve(wp['id'],'검산 담당자','독립 산술 정답과 거래별 대사',adjustment_rows(event),True)
        before = closing.summary(2026)['ending']
        # Offsetting corruption leaves the asset's total ending intact.
        with storage.connect() as c:
            entries = c.execute('SELECT id,amount FROM reserve_entries ORDER BY id').fetchall()
            for entry,delta in zip(entries,(Decimal(1),Decimal(-1))):
                c.execute('UPDATE reserve_entries SET amount=? WHERE id=?',(str(Decimal(entry['amount'])+delta),entry['id']))
        self.assertEqual(sum(map(Decimal,before.values())),sum(map(Decimal,closing.summary(2026)['ending'].values())))
        run_validation(2026,1)
        with self.assertRaisesRegex(ValueError,'전기 금액'):
            closing.close_year(2026,'검산 담당자','기말합계가 같아도 거래별 오류 차단',True,calculate(state,'2026-12-31')['positions'])
        self.assertIsNone(closing.summary(2026)['close'])

    def test_partial_sale_cancel_and_reapprove_preserves_net_reserve(self):
        self.isolated_database()
        state = state_for(CASES[0]); storage.save_state(state,0)
        reference = audit(state,'2026-12-31'); workpapers.generate(2026,1)
        papers = workpapers.list_workpapers(2026)['items']
        for wp in papers:
            candidate = wp['candidate']
            event = next(e for e in reference['events'] if (e['source_type'],e['source_id'])==(candidate['source_type'],candidate['source_id']))
            workpapers.approve(wp['id'],'검산 담당자','독립 산술 정답과 거래별 대사',adjustment_rows(event),True)
        sale = next(w for w in papers if w['candidate']['event']=='SELL')
        original = closing.summary(2026)
        workpapers.cancel_approval(sale['id'],'검산 담당자','부분 처분의 승인 근거 재확인')
        cancelled = closing.summary(2026)
        self.assertEqual(sum(map(Decimal,cancelled['ending'].values())),Decimal(-40))
        self.assertTrue(any(p['source_ref'].startswith('REVERSAL:') for p in cancelled['entries']))
        workpapers.generate(2026,1)
        for wp in workpapers.list_workpapers(2026)['items']:
            if wp['status']=='DRAFT':
                event = next(e for e in reference['events'] if e['event']=='SELL')
                workpapers.approve(wp['id'],'검산 담당자','부분 처분 정답으로 재승인',adjustment_rows(event),True)
        final = closing.summary(2026)
        self.assertEqual(final['ending'],original['ending'])
        self.assertGreater(len(final['entries']),len(original['entries']))
        run_validation(2026,1)
        self.assertIsNotNone(closing.close_year(2026,'검산 담당자','역분개·재승인 포함 대사',True,calculate(state,'2026-12-31')['positions'])['close'])
