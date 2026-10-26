"""Independent Decimal replay of persisted investment events.

Uses the documented P0 assumptions, not browser results or legal dispositions.
Amounts are parsed as Decimal; exact rational proportional operations avoid
repeating-division errors at half-cent boundaries. Published amounts use
ROUND_HALF_UP to two decimals; quantities are integers.
"""
import hashlib
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from fractions import Fraction

from .validation_engine import fiscal_year, year_end
from .fvoci_policy import policy_schedule, resolve_policy

ENGINE_VERSION = 'decimal-v1'
CENT = Decimal('0.01')


def number(value):
    try:
        if isinstance(value, bool) or value is None:
            raise ValueError()
        result = Decimal(str(value))
        if not result.is_finite() or abs(result) > Decimal('1e15'):
            raise ValueError()
        return result
    except (ValueError, TypeError, InvalidOperation):
        raise ValueError('서버 재계산: 유효하지 않은 수량·금액입니다.') from None


def money(value):
    if isinstance(value,Fraction):
        # Exact half-cent decisions even after repeated proportional disposals.
        scaled=value.numerator*100
        cents=(abs(scaled)*2+value.denominator)//(2*value.denominator)
        rounded=Decimal(cents if scaled>=0 else -cents).scaleb(-2)
        return format(rounded if rounded else Decimal(0),'.2f')
    rounded = number(value).quantize(CENT, rounding=ROUND_HALF_UP)
    return format(rounded if rounded else Decimal(0), '.2f')


def tax_method(state, year):
    rows = sorted((p for p in state.get('taxPolicyYears', []) if int(p['effectiveYear']) <= year),
                  key=lambda p: int(p['effectiveYear']))
    policy = rows[-1] if rows else state.get('taxPolicy', {})
    method = 'TOTAL' if policy.get('filingStatus') == 'NOT_FILED' else policy.get('method', 'TOTAL')
    if method not in ('TOTAL', 'MOVING'):
        raise ValueError('서버 재계산: 지원하지 않는 세무원가 정책입니다.')
    return method


def calculate(state, as_of):
    """Return canonical positions, event candidates and a reproducible digest."""
    with localcontext() as context:
        context.prec = 40
        return _calculate(state, as_of)


def _calculate(state, as_of):
    date.fromisoformat(as_of)
    month = int(state.get('fiscalEndMonth') or 12)
    if not 1 <= month <= 12:
        raise ValueError('결산월 오류')
    fy = lambda day: fiscal_year(day, month)
    baseline_policy, policy_rows = policy_schedule(state)
    assets, balances, groups = {}, {}, defaultdict(list)
    for asset in state.get('assets', []):
        aid = int(asset['id'])
        if aid <= 0 or aid in assets or asset.get('classification') not in ('FVOCI', 'FVTPL') or not asset.get('stock_code'):
            raise ValueError('서버 재계산: 자산 ID·종목·회계분류 오류')
        assets[aid] = asset
        groups[asset['stock_code']].append(aid)
        balances[aid] = dict(qty=0, book=Fraction(0), tax=Decimal(0), reserve=Decimal(0),
                             oci=Fraction(0), activeOCI=Fraction(0), pl=Fraction(0), dividend=Fraction(0))
    events, trades, seen = [], [], set()
    for table, kind in (('trades', 'trade'), ('valuations', 'valuation')):
        for raw in state.get(table, []):
            event = dict(raw)
            day = date.fromisoformat(event['date']).isoformat()
            eid, aid = int(event['id']), int(event['assetId'])
            if eid <= 0 or (kind, eid) in seen or aid not in assets:
                raise ValueError('서버 재계산: 중복 원천 ID 또는 미등록 자산')
            seen.add((kind, eid))
            event.update(id=eid, assetId=aid, date=day, kind=kind)
            if kind == 'trade':
                if event['type'] not in ('BUY', 'SELL', 'DIVIDEND'):
                    raise ValueError('서버 재계산: 거래유형 오류')
                event['amount'] = Fraction(number(event['amount']))
                if event['amount'] < 0:
                    raise ValueError('서버 재계산: 음수 거래금액')
                if event['type'] != 'DIVIDEND':
                    qty = number(event['qty'])
                    if qty <= 0 or qty != qty.to_integral_value():
                        raise ValueError('서버 재계산: 양의 정수 수량이 필요합니다.')
                    event['qty'] = int(qty)
                    if fy(day) <= fy(as_of):
                        trades.append(event)
            else:
                event['price'] = Fraction(number(event['price']))
                if event['price'] < 0:
                    raise ValueError('서버 재계산: 음수 평가가격')
            if day <= as_of:
                events.append(event)
    trades.sort(key=lambda e: (e['date'], e['id']))
    costs = {}
    for code, ids in groups.items():
        stock = [e for e in trades if e['assetId'] in ids]
        quantity, basis = 0, Fraction(0)
        for year in sorted({fy(e['date']) for e in stock}):
            annual = [e for e in stock if fy(e['date']) == year]
            method = tax_method(state, year)
            buys = [e for e in annual if e['type'] == 'BUY']
            total_qty = quantity + sum(e['qty'] for e in buys)
            unit = (basis + sum((e['amount'] for e in buys), Fraction(0))) / total_qty if total_qty else Fraction(0)
            for e in annual:
                if e['type'] == 'BUY':
                    quantity += e['qty']
                    basis += e['amount']
                else:
                    if e['qty'] > quantity:
                        raise ValueError('서버 재계산: 종목 통합 보유수량 초과 매도')
                    cost = e['qty'] * (unit if method == 'TOTAL' else basis / quantity)
                    costs[e['id']] = cost
                    basis -= cost
                    quantity -= e['qty']
            if method == 'TOTAL':
                basis = quantity * unit
            if not quantity:
                basis = Fraction(0)
    stock_basis = defaultdict(Fraction)
    lots = defaultdict(dict)
    ledger = []
    for e in sorted(events, key=lambda e: (e['date'], e['kind'] == 'valuation', e['id'])):
        policy = resolve_policy(baseline_policy,policy_rows,fy(e['date']))
        aid = e['assetId']
        asset, current = assets[aid], balances[aid]
        code, classification = asset['stock_code'], asset['classification']
        typ = e['type'] if e['kind'] == 'trade' else 'VALUATION'
        book_pl = tax_pl = change_oci = transfer = Fraction(0)
        if typ == 'BUY':
            current['qty'] += e['qty']
            current['book'] += e['amount']
            stock_basis[code] += e['amount']
            lots[aid][e['id']] = e['qty']
        elif typ == 'SELL':
            if e['qty'] > current['qty']:
                raise ValueError('서버 재계산: 자산 보유수량 초과 매도')
            method = state.get('stockPolicies', {}).get(code, {}).get('book', 'TOTAL')
            if method not in ('TOTAL', 'MOVING', 'FIFO', 'SPECIFIC'):
                raise ValueError('서버 재계산: 회계 원가배분 정책 오류')
            if method == 'SPECIFIC':
                allocations = e.get('lotAllocation') or []
                used = defaultdict(Decimal)
                for allocation in allocations:
                    used[int(allocation['id'])] += number(allocation['qty'])
                if sum(used.values()) != e['qty'] or any(q <= 0 or q > lots[aid].get(lot, 0) for lot, q in used.items()):
                    raise ValueError('서버 재계산: 개별법 LOT 배분 오류')
                for lot, qty in used.items():
                    lots[aid][lot] -= qty
            elif method == 'FIFO':
                left = e['qty']
                for lot, qty in lots[aid].items():
                    removed = min(left, qty)
                    lots[aid][lot] -= removed
                    left -= removed
            fraction = Fraction(e['qty'],current['qty'])
            removed_book = current['book'] * fraction
            tax_pl = e['amount'] - costs[e['id']]
            stock_basis[code] -= costs[e['id']]
            if classification == 'FVOCI':
                disposed_oci = current['activeOCI'] * fraction
                change_oci = e['amount'] - removed_book
                transfer = disposed_oci + change_oci if policy == 'TRANSFER' else Fraction(0)
                current['activeOCI'] -= disposed_oci
                current['oci'] += change_oci - transfer
            else:
                book_pl = e['amount'] - removed_book
            current['qty'] -= e['qty']
            current['book'] -= removed_book
            if not current['qty']:
                current['book'] = Fraction(0)
        elif typ == 'DIVIDEND':
            book_pl = tax_pl = e['amount']
            current['dividend'] += e['amount']
        else:
            if not current['qty']:
                raise ValueError('서버 재계산: 보유수량 없는 평가')
            target = current['qty'] * e['price']
            gain = target - current['book']
            current['book'] = target
            if classification == 'FVOCI':
                change_oci = gain
                current['oci'] += gain
                current['activeOCI'] += gain
            else:
                book_pl = gain
        current['pl'] += book_pl
        held = [i for i in groups[code] if balances[i]['qty']]
        total_qty = sum(balances[i]['qty'] for i in held)
        total_basis = Decimal(money(stock_basis[code])) if held else Decimal(0)
        assigned, effects = Decimal(0), []
        for i in groups[code]:
            b = balances[i]
            allocated = (total_basis - assigned if held and i == held[-1] else
                         Decimal(money(total_basis * b['qty'] / total_qty)) if b['qty'] else Decimal(0))
            assigned += allocated
            reserve = allocated - Decimal(money(b['book']))
            movement = reserve - b['reserve']
            b['tax'], b['reserve'] = allocated, reserve
            if movement or i == aid:
                effects.append(dict(assetId=i, tax=money(allocated), book=money(b['book']),
                                    reserve=money(reserve), reserveChange=money(movement)))
        own = next(effect for effect in effects if effect['assetId'] == aid)
        ledger.append(dict(asset_id=aid, stock_code=code, asset_name=asset.get('asset_name', code),
                           classification=classification, source_type=e['kind'], source_id=e['id'],
                           date=e['date'], event=typ, book_pl=money(book_pl), tax_pl=money(tax_pl),
                           oci_change=money(change_oci), oci_equity_transfer=money(transfer),
                           fvoci_disposal_policy=policy if classification == 'FVOCI' else None,
                           candidate_adjustment=money(tax_pl-book_pl), reserve_change=own['reserveChange'],
                           tax_basis=own['tax'], book_basis=own['book'], allocation_effects=effects,
                           recognition='OCI' if classification == 'FVOCI' else 'PL'))
    positions = []
    for aid, b in balances.items():
        positions.append(dict(assetId=aid, qty=b['qty'], **{k: money(b[k]) for k in
                              ('book','tax','reserve','oci','activeOCI','pl','dividend')},
                              retainedDisposalOCI=money(b['oci']-b['activeOCI'])))
    pools = [dict(stock_code=code, tax=money(stock_basis[code] if any(balances[i]['qty'] for i in ids) else 0))
             for code, ids in groups.items()]
    result = dict(engine_version=ENGINE_VERSION, as_of=as_of, positions=positions, ledger=ledger, stockPools=pools)
    result['digest'] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    return result


def calculate_year(state, year):
    return calculate(state, year_end(int(year), int(state.get('fiscalEndMonth') or 12)).isoformat())


def candidates_for_year(calculated, year, month):
    return [dict(row) for row in calculated['ledger'] if fiscal_year(row['date'], month) == int(year)
            and (row['event'] in ('SELL', 'VALUATION') or
                 any(number(e['reserveChange']) for e in row['allocation_effects']))]


def evidence(calculated, revision):
    return {k: calculated[k] for k in ('engine_version', 'digest', 'as_of')} | {'source_revision': revision}


def compare_positions(provided, calculated):
    if not isinstance(provided, list) or len(provided) != len(calculated['positions']):
        raise ValueError('서버 재계산 불일치: 모든 자산의 장부금액이 필요합니다.')
    by_id = {}
    for item in provided:
        aid = str(item.get('assetId'))
        if aid in by_id:
            raise ValueError('서버 재계산 불일치: 중복 자산')
        by_id[aid] = item
    for expected in calculated['positions']:
        item = by_id.get(str(expected['assetId']), {})
        for key in ('qty', 'book', 'tax'):
            if number(item.get(key)) != number(expected[key]):
                raise ValueError(f'서버 재계산 불일치: 자산 {expected["assetId"]} {key}')
        for key in ('reserve', 'oci', 'activeOCI', 'retainedDisposalOCI'):
            if key in item and number(item[key]) != number(expected[key]):
                raise ValueError(f'서버 재계산 불일치: 자산 {expected["assetId"]} {key}')


def compare_candidate(item, expected):
    for key in ('asset_id','source_type','source_id','classification','date','event'):
        if str(item.get(key)) != str(expected[key]):
            raise ValueError(f'서버 재계산 불일치: 검증표 {key}')
    for key in ('book_pl','tax_pl','oci_change','oci_equity_transfer','candidate_adjustment',
                'reserve_change','tax_basis','book_basis'):
        if number(item.get(key)) != number(expected[key]):
            raise ValueError(f'서버 재계산 불일치: 검증표 {key}')
