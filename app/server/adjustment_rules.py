"""Reviewable 2026 ordinary domestic listed-equity adjustment drafts.

Law establishes recognition/basis principles, not this software's row layout.
See docs/TAX_ADJUSTMENT_RULES_2026.md for sources, interpretation and exclusions.
No automatic approval; no rule is silently applied to unconfirmed facts.
"""
from collections import Counter
from decimal import Decimal

VERSION = 'kr-listed-2026-v1'
PROFILE = 'KR_ORDINARY_DOMESTIC_LISTED_2026'
CHECKED_ON = '2026-10-26'
SOURCES = [
    dict(title='법인세법 제18조제1호',edition='법률 제21217호 · 본문 표시 시행 2026-01-01',
         url='https://www.law.go.kr/LSW/lsSideInfoP.do?lsiSeq=280349&joNo=0018&joBrNo=00&docCls=jo&urlMode=lsScJoRltInfoR'),
    dict(title='법인세법 제22조',edition='법률 제21217호 · 조문정보 시행 2026-07-01',
         url='https://www.law.go.kr/LSW/lsLinkCommonInfo.do?lsJoLnkSeq=1018239731'),
    dict(title='법인세법 제42조',edition='법률 제21217호 · 조문정보 시행 2026-07-01',
         url='https://law.go.kr/LSW/lsLinkCommonInfo.do?chrClsCd=010202&lsJoLnkSeq=1030651181'),
    dict(title='법인세법 시행령 제75조',edition='대통령령 제36738호 · 시행 2026-10-01',
         url='https://www.law.go.kr/lsLinkCommonInfo.do?lsJoLnkSeq=1027290403'),
    dict(title='법인세법 시행령 제78조',edition='대통령령 제36738호 · 시행 2026-10-01',
         url='https://www.law.go.kr/LSW/lsSideInfoP.do?lsiSeq=290843&joNo=0078&joBrNo=00&docCls=jo&urlMode=lsScJoRltInfoR'),
    dict(title='법인세법 시행령 제106조',edition='대통령령 제36738호 · 시행 2026-10-01',
         url='https://www.law.go.kr/LSW/lsSideInfoP.do?lsiSeq=290843&joNo=0106&joBrNo=00&docCls=jo&urlMode=lsScJoRltInfoR'),
]
SCOPE_TEXT = ('일반 내국법인·국내 상장 보통주·12월 결산·정상 장내거래이며, '
              '부도/회생/부실징후/파산 감액, 특수관계, 집합투자/보험 특별계정, '
              '배당, 거래비용/세금효과, 기업행동, 지분법/연결 대상, 동일 종목 복수 자산 보유는 제외')
BASIS = {
    'valuation': '법인세법 제18조제1호·제22조·제42조, 시행령 제73·75·78조',
    'sale': '법인세법 제15·19·40·42조, 시행령 제11조제2호·제19조제2호·제68조제1항제5호·제75조',
    'reserve': '법인세법 제67조, 시행령 제106조제1항제2호; 자산 장부 차이 사후관리',
    'capital': '법인세법 제15·19·42조의 순자산·원가 원칙에 따른 자본 연결 해석; IFRS 9 B5.7.1',
}


def d(value):
    return Decimal(str(value))


def signed_total(rows):
    return sum((d(r['amount']) * (1 if r['direction'] in ('익금산입','손금불산입') else -1)
                for r in rows), Decimal(0))


def build_plan(state, year, item):
    from .rounding import profile_for_year,WON
    is_won=profile_for_year(state,year)==WON
    scope = state.get('taxAdjustmentScope') or {}
    enforced = scope.get('enabled') is True and scope.get('year') == int(year)
    reasons = []
    if not enforced or scope.get('profile') != PROFILE:
        reasons.append('2026 일반 내국법인·국내 상장주식 적용범위 확인을 저장하세요.')
    if int(year) != 2026 or int(state.get('fiscalEndMonth') or 12) != 12:
        reasons.append('이번 규칙은 2026-01-01~2026-12-31 사업연도에 한정합니다.')
    if scope.get('confirmed') is not True or not str(scope.get('reviewer','')).strip() or not str(scope.get('note','')).strip():
        reasons.append('적용요건·예외 제외 확인자와 근거가 필요합니다.')
    if scope.get('exceptions') not in ([],):
        reasons.append('예외 여부가 미확인이거나 예외 거래가 있습니다.')
    assets = state.get('assets', [])
    asset = next((a for a in assets if str(a['id']) == str(item['asset_id'])), {})
    if len([a for a in assets if a.get('stock_code') == asset.get('stock_code')]) != 1:
        reasons.append('동일 종목의 복수 자산·혼합 분류 배분은 산술 검산과 법정 조정행을 별도로 검토해야 합니다.')
    if item['event'] not in ('SELL','VALUATION') or item['classification'] not in ('FVOCI','FVTPL'):
        reasons.append('일반 평가·처분 이외 거래는 별도 검토 대상입니다.')
    source = next((r for r in state.get('trades' if item['source_type']=='trade' else 'valuations', [])
                   if str(r['id']) == str(item['source_id'])), {})
    history = [r for r in state.get('trades', [])+state.get('valuations', []) if str(r['assetId'])==str(item['asset_id'])]
    for record in [asset,source]+history:
        if any(record.get(k) for k in ('tax_exception','related_party','corporate_action','impairment_exception',
                                       'fees','transaction_cost','transactionCost','deferred_tax')):
            reasons.append('원천에 예외·부대비용·세금효과가 표시되어 자동 규칙을 적용하지 않습니다.')
            break
    if any(r.get('type')=='DIVIDEND' for r in history):
        reasons.append('배당이 포함된 자산은 수입배당금 익금불산입 등을 별도 검토해야 합니다.')
    if (asset.get('instrument_type','COMMON_STOCK')!='COMMON_STOCK' or asset.get('issuer_country','KR')!='KR'
            or asset.get('listed',True) is not True or asset.get('currency','KRW')!='KRW'):
        reasons.append('보통주·국내 발행·상장 여부가 이번 적용범위와 다릅니다.')
    if any(str(e['assetId']) != str(item['asset_id']) and d(e['reserveChange'])
           for e in item['allocation_effects']):
        reasons.append('교차 자산 배분은 자동 법정 조정 대상에서 제외합니다.')
    plan = dict(version=VERSION, checked_on=CHECKED_ON, fiscal_year=int(year), enforced=enforced,
                status='MANUAL_REQUIRED' if reasons else 'REVIEW_REQUIRED', scope=SCOPE_TEXT,
                sources=[dict(s) for s in SOURCES], interpretation='원인별 행 구성은 법령 원칙을 적용한 검토용 설계이며 공식 유권해석·신고 인증이 아닙니다.',
                reasons=reasons, rows=[], accounting=dict(book_pl=item['book_pl'], oci_change=item['oci_change'],
                equity_transfer=item['oci_equity_transfer'], tax_pl=item['tax_pl']),
                equity_transfer_tax_effect='0.00')
    if is_won:plan.update(rounding_profile=WON,equity_transfer_tax_effect='0')
    if reasons:
        return plan
    rows = plan['rows']

    def add(rule, label, value, reserve=False, positive='익금산입', negative='손금산입', basis='sale'):
        value = d(value)
        if not value:
            return
        rows.append(dict(rule_id=rule, label=label, asset_id=str(item['asset_id']),
                         direction=positive if value>0 else negative,
                         disposition=('유보' if value>0 else '△유보') if reserve else '기타',
                         amount=format(abs(value),'.0f' if is_won else '.2f'), reserve_movement=format(value if reserve else Decimal(0),'.0f' if is_won else '.2f'),
                         legal_basis=f'{BASIS[basis]}; {BASIS["reserve"] if reserve else BASIS["capital"]}; {VERSION} 적용요건 검토 필요'))

    change = d(item['oci_change'])
    movement = d(item['reserve_change'])
    if item['classification']=='FVOCI':
        # Keep current OCI and the reversal at disposal separate, even when they net.
        add('OCI_CAPITAL','당기 OCI 자본 연결',change,basis='valuation')
        add('OCI_BASIS','당기 OCI 평가 부인',-change,True,basis='valuation')
        if item['event']=='SELL':
            add('DISPOSAL_RELEASE','처분 후 유보 환입·원가 차이',movement+change,True)
    elif item['event']=='VALUATION':
        add('PL_VALUATION','당기손익 평가 부인',movement,True,
            positive='손금불산입',negative='익금불산입',basis='valuation')
    else:
        add('PL_DISPOSAL','당기손익 처분원가 차이',movement,True)
    if signed_total(rows) != d(item['candidate_adjustment']):
        plan.update(status='MANUAL_REQUIRED',reasons=['반올림 또는 연결금액 불일치: 금액을 임의로 기타에 배분하지 않습니다.'],rows=[])
    if sum((d(r['reserve_movement']) for r in plan['rows']),Decimal(0)) != movement and plan['rows']:
        raise ValueError('조정규칙 내부 유보 대사 오류')
    return plan


def verify_adjustments(rows, item, plan):
    """Check income direction as well as signed reserve movements at approve/close."""
    for row in rows:
        if plan.get('rounding_profile')=='KRW_WON_V1' and any(d(row.get(k,0))!=d(row.get(k,0)).to_integral_value() for k in ('amount','reserve_movement')):
            raise ValueError('원 단위 정책의 조정 행에는 정수 원 금액이 필요합니다.')
        if row['disposition'] in ('유보','△유보'):
            sign = 1 if row['direction'] in ('익금산입','손금불산입') else -1
            if d(row['reserve_movement']) * sign <= 0:
                raise ValueError('조정 방향과 유보 증감 부호가 다릅니다.')
    if signed_total(rows) != d(item['candidate_adjustment']):
        raise ValueError('조정 행의 과세소득 증감 합계가 서버 계산과 다릅니다.')
    if plan['enforced'] and plan['status']=='MANUAL_REQUIRED':
        raise ValueError('자동 규칙 적용 제외: '+ '; '.join(plan['reasons']))
    if plan['status']=='REVIEW_REQUIRED':
        def signature(r):
            return (str(r.get('asset_id',item['asset_id'])),r['direction'],r['disposition'],
                    d(r['amount']),d(r.get('reserve_movement',0)))
        if Counter(map(signature,rows)) != Counter(map(signature,plan['rows'])):
            raise ValueError('2026 조정 규칙의 원인별 복수 행과 다릅니다. 누락·상계·중복·소득처분을 확인하세요.')
