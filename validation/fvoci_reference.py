"""Independent single-position Decimal oracle for documented FVOCI examples.

Test support only. No browser imports, no legal income-disposition decision,
no server approval integration. Proceeds are assumed to equal fair value.
"""
from decimal import Decimal


def calculate(state, as_of, disposal):
    if len(state['assets']) != 1:
        raise ValueError('Reference scope: one position')
    if disposal not in ('TRANSFER', 'RETAIN'):
        raise ValueError('Unknown disposal policy')
    d = lambda value: Decimal(str(value))
    month = state.get('fiscalEndMonth', 12)
    fy = lambda date: int(date[:4]) + (int(date[5:7]) > month)
    trades = state['trades']
    method = state.get('taxPolicy', {}).get('method', 'TOTAL')
    units = {}
    opening_qty, opening_cost = 0, d(0)
    if method == 'TOTAL':
        for year in sorted({fy(t['date']) for t in trades}):
            annual = [t for t in trades if fy(t['date']) == year]
            qty = opening_qty + sum(t['qty'] for t in annual if t['type'] == 'BUY')
            cost = opening_cost + sum((d(t['amount']) for t in annual if t['type'] == 'BUY'), d(0))
            units[year] = cost / qty if qty else d(0)
            opening_qty = qty - sum(t['qty'] for t in annual if t['type'] == 'SELL')
            opening_cost = units[year] * opening_qty
    events = [(t['date'], 0, t['id'], t) for t in trades]
    events += [(v['date'], 1, v['id'], v) for v in state['valuations']]
    qty = 0
    book = tax = active = oci = d(0)
    for date, kind, _, event in sorted(events):
        if date > as_of:
            continue
        if kind == 1:
            change = d(event['price']) * qty - book
            book += change
            active += change
            oci += change
        elif event['type'] == 'BUY':
            qty += event['qty']
            book += d(event['amount'])
            tax += d(event['amount'])
        elif event['type'] == 'SELL':
            fraction = d(event['qty']) / qty
            removed_book = book * fraction
            removed_tax = units[fy(date)] * event['qty'] if method == 'TOTAL' else tax * fraction
            disposed_oci = active * fraction
            final_change = d(event['amount']) - removed_book
            transfer = disposed_oci + final_change if disposal == 'TRANSFER' else d(0)
            oci += final_change - transfer
            active -= disposed_oci
            book -= removed_book
            tax -= removed_tax
            qty -= event['qty']
    return dict(qty=qty, book=book, tax=tax, reserve=tax-book, activeOCI=active, oci=oci)
