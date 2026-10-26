import unittest
from decimal import Decimal
from validation.engine import Event, calculate, D

class ReferenceTests(unittest.TestCase):
    def test_three_year_fvtpl_and_reserve_reversal(self):
        r=calculate([
            Event('2024-01-10','BUY',100,D(10000000)),
            Event('2024-12-31','VALUE',price=D(130000)),
            Event('2025-12-31','VALUE',price=D(110000)),
            Event('2026-03-01','SELL',100,D(12000000))])
        self.assertEqual([x['adjustment'] for x in r['rows']], [D(0),D(-3000000),D(2000000),D(1000000)])
        self.assertEqual([r['years'][y]['reserve'] for y in ('2024','2025','2026')], [D(-3000000),D(-1000000),D(0)])
        self.assertEqual(sum(x['book_pl'] for x in r['rows']),D(2000000))
        self.assertEqual(sum(x['tax_pl'] for x in r['rows']),D(2000000))

    def test_partial_disposal_preserves_basis_difference(self):
        r=calculate([Event('2024-01-01','BUY',100,D(1000000)),
                     Event('2024-12-31','VALUE',price=D(15000)),
                     Event('2025-02-01','SELL',40,D(640000))])
        s=r['rows'][-1]
        self.assertEqual((s['qty'],s['book'],s['tax'],s['reserve']), (60,D(900000),D(600000),D(-300000)))
        self.assertEqual(s['adjustment'],D(200000))

    def test_moving_and_total_differ_with_late_purchase(self):
        e=[Event('2024-01-01','BUY',100,D(1000000)),
           Event('2024-06-01','SELL',50,D(800000)),
           Event('2024-09-01','BUY',100,D(2000000))]
        m=calculate(e,'MOVING')['rows'][1]['tax_pl']
        t=calculate(e,'TOTAL')['rows'][1]['tax_pl']
        self.assertEqual(m,D(300000))
        self.assertEqual(t,D(50000))

    def test_dividend_and_over_sale(self):
        r=calculate([Event('2024-01-01','BUY',2,D(200)),Event('2024-02-01','DIVIDEND',amount=D(30))])
        self.assertEqual(r['rows'][-1]['adjustment'],D(0))
        self.assertEqual(r['rows'][-1]['book_pl'],D(30))
        with self.assertRaises(ValueError):
            calculate([Event('2024-01-01','SELL',1,D(100))])

    def test_full_disposal_and_rounding(self):
        r=calculate([Event('2024-01-01','BUY',3,D(100)),Event('2024-02-01','SELL',1,D(40)),Event('2024-03-01','SELL',2,D(80))])
        self.assertEqual(r['rows'][-1]['tax'],D(0))
        self.assertEqual(r['rows'][-1]['qty'],0)

if __name__=='__main__': unittest.main()
