"""User-selected whole-won arithmetic; unmarked historical inputs stay legacy."""
from decimal import Decimal
from fractions import Fraction

LEGACY='LEGACY_CENT_V1'
WON='KRW_WON_V1'


def policy(state):
    value=state.get('roundingPolicy')
    if value is None:return None
    if (not isinstance(value,dict) or value.get('profile')!=WON or
            type(value.get('effectiveYear')) is not int or not 1900<=value['effectiveYear']<=2200):
        raise ValueError('원 단위 반올림 정책·적용 시작 사업연도를 확인하세요.')
    return value


def profile_for_year(state,year):
    row=policy(state)
    return WON if row and int(year)>=row['effectiveYear'] else LEGACY


def rounded(value,digits=0):
    """Exact ties away from zero, without Decimal context or binary epsilon."""
    value=Fraction(value);scaled=value.numerator*10**digits
    units=(abs(scaled)*2+value.denominator)//(2*value.denominator)
    return Fraction(units if scaled>=0 else -units,10**digits)


def whole(value):
    result=rounded(value)
    if abs(result)>9007199254740991:
        raise ValueError('원 단위 금액이 화면의 정확한 정수 표현 범위를 초과했습니다.')
    return result


def portion(value,balance):
    """Never consume more than the signed posted balance on partial disposal."""
    return min(max(whole(value),min(Fraction(0),balance)),max(Fraction(0),balance))


def ensure_profile(c):
    """Append a named immutable profile to existing v2 files; no DDL/migration."""
    import json
    from datetime import datetime,timezone
    c.execute('INSERT OR IGNORE INTO rounding_profiles VALUES(?,?,?,?,?,?,?)',
              (WON,'사용자 선택 원 단위 반올림; 법정 신고 단위 확정 아님',
               json.dumps(dict(output_scale=0,rounding='HALF_AWAY_FROM_ZERO',average='EXACT_RATIONAL',
                               disposal_residual='LAST_DISPOSAL',allocation_residual='LAST_HELD_ASSET')),
               'APPROVED','user-selected','사용자가 승인한 계산 정책',datetime.now(timezone.utc).isoformat()))
