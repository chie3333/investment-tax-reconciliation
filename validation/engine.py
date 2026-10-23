"""Independent Decimal-based reference calculator for FVTPL verification.
Illustrative Korean tax assumption: unrealized equity fair-value changes are
not recognized for current taxable income; realized proceeds less tax cost are.
NOT a tax filing engine, and not an IFRS/FVOCI policy opinion.
"""
from dataclasses import dataclass
from decimal import Decimal
from collections import defaultdict

D = lambda x: Decimal(str(x))

@dataclass(frozen=True)
class Event:
    date: str
    kind: str  # BUY, SELL, VALUE, DIVIDEND
    qty: int = 0
    amount: Decimal = Decimal('0')  # total cash consideration (BUY/SELL/DIVIDEND)
    price: Decimal = Decimal('0')   # closing per-share price for VALUE


def calculate(events, tax_method='MOVING'):
    """Return event rows and year-end snapshots; assumes one FVTPL position.

    TOTAL uses opening-year tax cost plus all purchases in year / shares;
    a year must be complete before its annual average is final. Prices,
    fees and tax treatments are supplied, never retrieved.
    """
    if tax_method not in ('MOVING', 'TOTAL'):
        raise ValueError('tax_method must be MOVING or TOTAL')
    ev = sorted(events, key=lambda x: (x.date, {'BUY':0,'SELL':1,'DIVIDEND':2,'VALUE':3}[x.kind]))
    if any(e.qty < 0 or (e.kind in ('BUY','SELL') and e.qty == 0) or e.amount < 0 or e.price < 0 for e in ev):
        raise ValueError('invalid quantity or price')
    # Annual average is retrospective; compute from year-opening tax position.
    annual = {}
    if tax_method == 'TOTAL':
        opening_q, opening_cost = 0, D(0)
        for year in sorted(set(e.date[:4] for e in ev)):
            group = [e for e in ev if e.date.startswith(year)]
            buys = [e for e in group if e.kind == 'BUY']
            sells = [e for e in group if e.kind == 'SELL']
            qty = opening_q + sum(e.qty for e in buys)
            cost = opening_cost + sum((e.amount for e in buys), D(0))
            unit = cost / qty if qty else D(0)
            annual[year] = unit
            opening_q = qty - sum(e.qty for e in sells)
            if opening_q < 0: raise ValueError('annual sales exceed holdings')
            opening_cost = unit * opening_q
    q = 0
    book = tax = D(0)
    rows, years = [], {}
    prev_year = None
    for e in ev:
        year = e.date[:4]
        if prev_year is not None and year != prev_year:
            years[prev_year] = snapshot(q, book, tax)
        prev_year = year
        old_book, old_tax = book, tax
        book_pl = tax_pl = oci = D(0)
        if e.kind == 'BUY':
            q += e.qty; book += e.amount; tax += e.amount
        elif e.kind == 'VALUE':
            if q <= 0: raise ValueError('cannot value zero holdings')
            target = e.price * q
            book_pl = target - book
            book = target
        elif e.kind == 'SELL':
            if e.qty > q: raise ValueError('sale exceeds holdings')
            removed_book = book * D(e.qty) / D(q)
            removed_tax = (tax * D(e.qty) / D(q) if tax_method == 'MOVING'
                           else annual[year] * e.qty)
            book -= removed_book; tax -= removed_tax; q -= e.qty
            book_pl = e.amount - removed_book
            tax_pl = e.amount - removed_tax
            if q == 0:
                if abs(tax) > D('0.00000001'): raise ValueError('tax cost remainder on full disposal')
                book = tax = D(0)
        elif e.kind == 'DIVIDEND':
            book_pl = tax_pl = e.amount  # no dividend received deduction modeled
        else: raise ValueError('unknown event')
        adj = tax_pl - book_pl
        reserve_delta = (tax - book) - (old_tax - old_book)
        rows.append(dict(date=e.date, kind=e.kind, qty=q, book=book, tax=tax,
                         book_pl=book_pl, tax_pl=tax_pl, adjustment=adj,
                         reserve=tax-book, reserve_delta=reserve_delta, oci=oci))
    if prev_year: years[prev_year] = snapshot(q, book, tax)
    return {'rows':rows, 'years':years}


def snapshot(q, book, tax):
    return {'qty':q, 'book':book, 'tax':tax, 'reserve':tax-book}
