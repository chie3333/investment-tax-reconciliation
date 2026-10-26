"""Test-only reserve oracle, independent of both application calculation engines.

Reconstructs tax expenses from annual purchase totals / moving pools, then
rebuilds every event's balance sheet. Reserve movements are differences of
those snapshots, NOT candidate amounts copied from an application engine.
Exact fractions and integer-cent rounding avoid binary float arithmetic.
Scope: one stock, ordinary buys/sales/valuations, one constant tax method, no costs,
corporate actions or legal income-disposition assertions.
"""
from fractions import Fraction


def cents(value):
    value = Fraction(str(value)) if not isinstance(value, Fraction) else value
    absolute = abs(value) * 100
    integer = (2 * absolute.numerator + absolute.denominator) // (2 * absolute.denominator)
    return Fraction(integer if value >= 0 else -integer, 100)


def audit(state, as_of):
    method = state.get('taxPolicy', {}).get('method', 'TOTAL')
    if method not in ('TOTAL', 'MOVING') or state.get('taxPolicyYears'):
        raise ValueError('Reference requires a constant TOTAL or MOVING method')
    month = int(state.get('fiscalEndMonth', 12))
    year = lambda day: int(day[:4]) + (int(day[5:7]) > month)
    assets = {a['id']: a for a in state['assets']}
    groups = {a['stock_code']: [i for i, b in assets.items() if b['stock_code'] == a['stock_code']]
              for a in assets.values()}
    if len(groups) != 1:
        raise ValueError('Reference requires exactly one stock pool')
    trades = sorted(state['trades'], key=lambda t: (t['date'], t['id']))
    expenses = {}
    for code, ids in groups.items():
        history = [t for t in trades if t['assetId'] in ids and year(t['date']) <= year(as_of)]
        if any(t['type'] not in ('BUY', 'SELL') for t in history):
            raise ValueError('Reference supports BUY/SELL only')
        opening_qty, opening_cost = 0, Fraction(0)
        for fiscal in sorted({year(t['date']) for t in history}):
            annual = [t for t in history if year(t['date']) == fiscal]
            purchases = [t for t in annual if t['type'] == 'BUY']
            available = opening_qty + sum(t['qty'] for t in purchases)
            cost = opening_cost + sum((Fraction(str(t['amount'])) for t in purchases), Fraction(0))
            average = cost / available if available else Fraction(0)
            quantity, running = opening_qty, opening_cost
            for t in annual:
                if t['type'] == 'BUY':
                    quantity += t['qty']; running += Fraction(str(t['amount']))
                else:
                    expense = t['qty'] * (average if method == 'TOTAL' else running / quantity)
                    expenses[t['id']] = expense
                    quantity -= t['qty']; running -= expense
            opening_qty = available - sum(t['qty'] for t in annual if t['type'] == 'SELL')
            opening_cost = cost - sum((expenses[t['id']] for t in annual if t['type'] == 'SELL'), Fraction(0))
    quantity = {i: 0 for i in assets}
    book = {i: Fraction(0) for i in assets}
    previous = {i: dict(qty=0, book=Fraction(0), tax=Fraction(0), reserve=Fraction(0)) for i in assets}
    executed, events = [], []
    ordered = [(t['date'], 0, t['id'], t) for t in trades]
    ordered += [(v['date'], 1, v['id'], v) for v in state.get('valuations', [])]
    for day, kind, eid, event in sorted(ordered):
        if day > as_of:
            continue
        aid = event['assetId']; code = assets[aid]['stock_code']; ids = groups[code]
        before = {i: dict(p) for i, p in previous.items()}
        old_book = book[aid]
        tax_cost = Fraction(0)
        book_pl = Fraction(0)
        removed = Fraction(0)
        if kind:
            book[aid] = quantity[aid] * Fraction(str(event['price']))
            if assets[aid]['classification'] == 'FVTPL':
                book_pl = book[aid] - old_book
            typ = 'VALUATION'
        else:
            executed.append(event)
            typ = event['type']
            if typ == 'BUY':
                quantity[aid] += event['qty']; book[aid] += Fraction(str(event['amount']))
            else:
                removed = old_book * event['qty'] / quantity[aid]
                quantity[aid] -= event['qty']; book[aid] = old_book - removed
                tax_cost = expenses[eid]
                if assets[aid]['classification'] == 'FVTPL':
                    book_pl = Fraction(str(event['amount'])) - removed
        # Pool = all executed purchases minus independently derived sale costs.
        pool = sum((Fraction(str(t['amount'])) if t['type'] == 'BUY' else -expenses[t['id']]
                    for t in executed if t['assetId'] in ids), Fraction(0))
        held = [i for i in ids if quantity[i]]
        total_qty = sum(quantity[i] for i in held)
        allocated = {}
        for i in held[:-1]:
            allocated[i] = cents(cents(pool) * quantity[i] / total_qty)
        if held:
            allocated[held[-1]] = cents(pool) - sum(allocated.values(), Fraction(0))
        for i in ids:
            tax = allocated.get(i, Fraction(0))
            previous[i] = dict(qty=quantity[i], book=cents(book[i]), tax=tax, reserve=tax-cents(book[i]))
        movements = {i: previous[i]['reserve']-before[i]['reserve'] for i in ids}
        tax_pl = Fraction(str(event['amount']))-tax_cost if typ == 'SELL' else Fraction(0)
        events.append(dict(date=day, source_type='valuation' if kind else 'trade', source_id=eid,
                           asset_id=aid, event=typ, before=before, after={i: dict(p) for i,p in previous.items()},
                           movements=movements, removed_book=cents(removed), tax_cost=cents(tax_cost),
                           tax_pl=cents(tax_pl), book_pl=cents(book_pl), pool_tax=cents(pool) if held else Fraction(0)))
    return dict(events=events, positions=previous)
