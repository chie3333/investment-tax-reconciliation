"""Server-owned validation snapshots, atomically bound to saved revisions."""
import json
from collections import defaultdict
from decimal import Decimal
from .calculation import calculate_year, compare_positions, evidence
from .storage import LOCK, connect, read_state, save_validation
from .validation_engine import validate


def validation_result(state, year, calculated, browser=None, connection=None):
    comparison = dict(calculated)
    if connection is not None and connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fiscal_closes'").fetchone():
        prior = connection.execute('SELECT ending_json FROM fiscal_closes WHERE fiscal_year=?',(year-1,)).fetchone()
        if prior:
            balances = defaultdict(Decimal)
            for key, amount in json.loads(prior['ending_json']).items():
                balances[key.split('|')[0]] += Decimal(amount)
            opening = calculate_year(state,year-1)
            comparison['prior_reserves'] = {str(p['assetId']):str(balances[str(p['assetId'])]) for p in opening['positions']}
            comparison['opening_reserves'] = {str(p['assetId']):p['reserve'] for p in opening['positions']}
    result = validate(state, year, comparison)
    if browser is not None:
        try:
            compare_positions(browser.get('positions'), calculated)
        except (ValueError, TypeError, AttributeError) as exc:
            result['issues'].append(dict(code='SERVER_CALC_MISMATCH', severity='ERROR',
                title='브라우저·서버 재계산 불일치', detail=str(exc), ref=str(year), expected=None, actual=None))
            result['summary']['ERROR'] += 1
    return result


def run_validation(year, revision, browser=None):
    year = int(year)
    if not 1900 <= year <= 2200:
        raise ValueError('사업연도 범위 오류')
    with LOCK, connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current = read_state(c)
        if revision != current['revision']:
            raise ValueError('저장 버전이 달라 검증을 중단했습니다.')
        state = current['state'] or {}
        calculated = calculate_year(state, year)
        result = validation_result(state, year, calculated, browser, c)
        result['calculation'] = evidence(calculated, revision)
        result['positions'] = calculated['positions']
        result['run_id'] = save_validation(result, revision, c)
        return result
