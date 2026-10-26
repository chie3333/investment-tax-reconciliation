"""Effective-fiscal-year OCI disposal presentation policy; legacy baseline stays intact."""


def policy_schedule(state):
    baseline = (state.get('fvociPolicy') or {}).get('disposal', 'TRANSFER')
    if baseline not in ('TRANSFER','RETAIN'):
        raise ValueError('FVOCI 처분 정책 오류')
    rows = state.get('fvociPolicyYears', [])
    if not isinstance(rows,list):
        raise ValueError('FVOCI 사업연도별 정책은 배열이어야 합니다.')
    seen = set()
    for row in rows:
        if not isinstance(row,dict):
            raise ValueError('FVOCI 사업연도별 정책 형식 오류')
        year = row.get('effectiveYear')
        if type(year) is not int or not 1900 <= year <= 2200 or year in seen:
            raise ValueError('FVOCI 적용 시작 사업연도 오류 또는 중복')
        seen.add(year)
        if row.get('disposal') not in ('TRANSFER','RETAIN'):
            raise ValueError('FVOCI 처분 정책 오류')
        if not str(row.get('reviewer') or '').strip() or not str(row.get('reason') or '').strip():
            raise ValueError('FVOCI 사업연도별 정책 확인자와 사유가 필요합니다.')
    return baseline, sorted(rows,key=lambda r:r['effectiveYear'])


def resolve_policy(baseline, rows, year):
    applicable = [row for row in rows if row['effectiveYear'] <= int(year)]
    return applicable[-1]['disposal'] if applicable else baseline


def policy_for_year(state, year):
    return resolve_policy(*policy_schedule(state),year)
