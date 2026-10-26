"""Independent, conservative closing controls; not a legal/tax opinion."""
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation


def D(value):
    try: return Decimal(str(value if value is not None else 0))
    except (InvalidOperation, ValueError): return Decimal('NaN')


def fiscal_year(day, month):
    d = date.fromisoformat(day)
    return d.year + (d.month > month)


def year_end(year, month):
    import calendar
    return date(year, month, calendar.monthrange(year, month)[1])


def validate(state, year, calculated=None):
    issues=[]
    def add(code, severity, title, detail, ref='', expected=None, actual=None):
        issues.append(dict(code=code,severity=severity,title=title,detail=detail,ref=str(ref),expected=str(expected) if expected is not None else None,actual=str(actual) if actual is not None else None))
    month=int(state.get('fiscalEndMonth') or 12)
    if not 1 <= month <= 12: raise ValueError('결산월이 올바르지 않습니다.')
    end=year_end(int(year),month)
    assets={str(a.get('id')):a for a in state.get('assets',[])}
    trades=sorted(state.get('trades',[]),key=lambda t:(t.get('date',''),int(t.get('id') or 0)))
    balances=defaultdict(Decimal); by_stock=defaultdict(Decimal)
    seen=set(); fingerprints=set()
    for t in trades:
        tid=t.get('id'); aid=str(t.get('assetId')); key=(t.get('date'),aid,t.get('type'),str(t.get('qty')),str(t.get('price')),str(t.get('amount')))
        if tid in seen: add('QTY_DUPLICATE_ID','ERROR','거래 ID 중복','동일한 거래 ID가 반복됩니다.',tid)
        seen.add(tid)
        if key in fingerprints:add('QTY_DUPLICATE_TRADE','WARNING','동일 거래 의심','동일 일자·종목·유형·수량·단가·금액의 거래가 반복됩니다. 실제 복수 체결 여부를 확인하세요.',tid)
        fingerprints.add(key)
        if aid not in assets: add('QTY_UNKNOWN_ASSET','ERROR','미등록 투자자산','거래에 연결된 자산이 없습니다.',tid);continue
        try: fy=fiscal_year(t['date'],month)
        except (ValueError,KeyError,TypeError): add('QTY_BAD_DATE','ERROR','거래일 오류','거래일을 확인하세요.',tid);continue
        if date.fromisoformat(t['date'])>end:continue
        typ=t.get('type'); qty=D(t.get('qty')); price=D(t.get('price')); amt=D(t.get('amount'))
        if not amt.is_finite() or amt<0: add('QTY_BAD_AMOUNT','ERROR','거래금액 오류','금액이 유효하지 않습니다.',tid)
        if typ in ('BUY','SELL'):
            if not qty.is_finite() or qty<=0 or qty!=qty.to_integral_value(): add('QTY_BAD_QUANTITY','ERROR','수량 오류','양의 정수 수량이 필요합니다.',tid);continue
            if not price.is_finite() or price<0: add('QTY_BAD_PRICE','ERROR','단가 오류','유효한 단가가 필요합니다.',tid)
            elif amt.is_finite() and abs(amt-qty*price)>Decimal('1'): add('QTY_AMOUNT_MISMATCH','ERROR','거래금액 불일치','수량 × 단가와 입력금액이 다릅니다.',tid,qty*price,amt)
            code=assets[aid].get('stock_code','')
            if typ=='BUY': balances[aid]+=qty;by_stock[code]+=qty
            else:
                if qty>balances[aid]:add('QTY_OVERSELL','ERROR','보유수량 초과 매도','회계 보유분의 수량을 초과했습니다.',tid,balances[aid],qty)
                if qty>by_stock[code]:add('QTY_STOCK_OVERSELL','ERROR','종목 통합수량 초과 매도','동일 종목의 전체 수량을 초과했습니다.',tid,by_stock[code],qty)
                balances[aid]-=qty;by_stock[code]-=qty
        elif typ!='DIVIDEND':add('QTY_BAD_TYPE','ERROR','거래유형 오류','지원하지 않는 거래유형입니다.',tid)
    for aid,a in assets.items():
        q=balances[aid]
        if q<0:add('QTY_NEGATIVE','ERROR','기말 보유수량 음수','거래 내역을 확인하세요.',aid,0,q)
        if q<=0:continue
        vals=[v for v in state.get('valuations',[]) if str(v.get('assetId'))==aid and v.get('date')==end.isoformat()]
        if not vals:add('VAL_MISSING','ERROR','기말 공정가치 평가 누락',f'{end} 결산일 평가기록이 없습니다. 휴장일이면 기준 시세일을 별도로 확인하세요.',aid)
        if len(vals)>1:add('VAL_DUPLICATE','ERROR','기말 평가 중복','같은 자산·결산일에 평가기록이 중복됩니다.',aid)
        for v in vals:
            pd=v.get('priceDate') or v.get('price_date')
            if pd:
                try:
                    price_day=date.fromisoformat(pd)
                    if price_day>end:add('VAL_FUTURE_PRICE','ERROR','결산일 이후 시세','결산일 이후 가격을 사용했습니다.',v.get('id'))
                    elif (end-price_day).days>7:add('VAL_STALE_PRICE','WARNING','오래된 평가가격','가격 기준일이 결산일보다 7일 넘게 이전입니다.',v.get('id'))
                except ValueError:add('VAL_PRICE_DATE_INVALID','ERROR','가격 기준일 형식 오류','가격 기준일은 YYYY-MM-DD여야 합니다.',v.get('id'))
            else:add('VAL_PRICE_DATE_UNVERIFIED','INFO','시세 기준일 미기록','시세의 실제 기준일과 출처를 확인할 수 없습니다.',v.get('id'))
            if not D(v.get('price')).is_finite() or D(v.get('price'))<=0:add('VAL_INVALID_PRICE','ERROR','평가가격 오류','평가가격이 유효한 양수가 아닙니다.',v.get('id'))
            if D(v.get('qty'))!=q:add('VAL_QTY_MISMATCH','ERROR','평가수량 불일치','기말 보유수량과 평가기록의 수량이 다릅니다.',v.get('id'),q,v.get('qty'))
            if abs(D(v.get('price'))*q-D(v.get('fairValue')))>Decimal('1'):add('VAL_AMOUNT_MISMATCH','ERROR','평가금액 불일치','수량 × 가격과 평가금액이 다릅니다.',v.get('id'))
    # OCI is not itself a taxable-income line. FVOCI equity disposal and
    # valuation require a documented income-disposition / tax-basis review.
    for aid, a in assets.items():
        if a.get('classification') != 'FVOCI': continue
        has_disposal=any(str(t.get('assetId'))==aid and t.get('type')=='SELL' and t.get('date','')<=end.isoformat() and fiscal_year(t['date'],month)==year for t in trades if t.get('date'))
        has_valuation=any(str(v.get('assetId'))==aid and v.get('date','')<=end.isoformat() and fiscal_year(v['date'],month)==year for v in state.get('valuations',[]) if v.get('date'))
        if has_disposal or has_valuation:
            add('OCI_TAX_DISPOSITION_REVIEW','WARNING','FVOCI 세무조정 검토 필요','FVOCI 평가·처분의 과세소득 반영과 소득처분(유보·기타 등)은 자동 확정하지 않습니다. 세무조정 근거와 원장 반영을 확인하세요.',aid)
    # Policy check: do not infer legal validity merely from a user-entered CONFIRMED flag.
    policies=sorted(state.get('taxPolicyYears') or [],key=lambda p:int(p.get('effectiveYear') or 0))
    applicable=[p for p in policies if int(p.get('effectiveYear') or 0)<=year]
    if not applicable:add('POL_MISSING','WARNING','사업연도 정책 미등록','해당 연도에 적용할 신고 정책이 없습니다.',year)
    else:
        p=applicable[-1]
        if p.get('filingStatus')=='UNKNOWN' or p.get('reviewStatus')!='CONFIRMED':add('POL_UNVERIFIED','WARNING','신고 정책 미확인','신고 상태 또는 증빙 검토가 완료되지 않았습니다.',year)
        if p.get('filingStatus')=='NOT_FILED' and p.get('method')!='TOTAL':add('POL_DEFAULT','ERROR','미신고 기본방법 오류','미신고인데 총평균법 이외 방법이 지정되었습니다.',year)
        if p.get('changeStatus')=='PENDING':add('POL_CHANGE_PENDING','WARNING','변경신고 확인 필요','변경신고 여부가 확정되지 않았습니다.',year)
        if p.get('changeStatus')=='YES' and not all(p.get(k) for k in ('previousMethod','changeDate','receipt')):add('POL_CHANGE_DOC','WARNING','변경신고 증빙 누락','변경 전 방법, 변경신고일 또는 접수번호가 없습니다.',year)
        if len(applicable)>1 and applicable[-2].get('method')!=p.get('method') and (p.get('changeStatus')!='YES' or p.get('previousMethod')!=applicable[-2].get('method')):add('POL_CHANGE_INCONSISTENT','ERROR','방법 변경 이력 불일치','직전 방법과 변경신고 기록이 일치하지 않습니다.',year)
    # Compare engine's position values against independent quantity and tax-book reconciliation.
    if calculated is not None:
        pos=calculated.get('positions',[])
        for j in calculated.get('journals',[]):
            debit=sum((D(x.get('debit')) for x in j.get('lines',[])),Decimal(0))
            credit=sum((D(x.get('credit')) for x in j.get('lines',[])),Decimal(0))
            if not debit.is_finite() or not credit.is_finite() or abs(debit-credit)>Decimal('1'):
                add('JRN_UNBALANCED','ERROR','분개 차대 불일치','분개의 차변·대변 합계가 일치하지 않습니다.',j.get('id'),debit,credit)
        if 'journals' not in calculated:add('JRN_UNVERIFIED','WARNING','분개 대사 미검증','계산 엔진에서 분개 내역을 제공하지 않아 차대 대사를 수행하지 못했습니다.',year)
        for stock in calculated.get('stockPools',[]):
            stock_id=stock.get('stock_code')
            allocated=sum((D(x.get('tax')) for x in pos if assets.get(str(x.get('assetId')),{}).get('stock_code')==stock_id),Decimal(0))
            pooled=D(stock.get('tax'))
            if allocated.is_finite() and pooled.is_finite() and abs(allocated-pooled)>Decimal('1'):
                add('REC_STOCK_TAX_POOL','ERROR','종목 통합 세무원가 배분 불일치','회계분류별 배분 세무원가 합계와 종목 통합 세무원가가 다릅니다.',stock_id,pooled,allocated)
        if 'stockPools' not in calculated:add('REC_STOCK_POOL_UNVERIFIED','WARNING','통합 세무원가 대사 미검증','종목별 독립 통합원가 총액이 제공되지 않아 배분 합계를 검증하지 못했습니다.',year)

        for p in pos:
            aid=str(p.get('assetId')); q=D(p.get('qty')); book=D(p.get('book')); tax=D(p.get('tax')); reserve=D(p.get('reserve'))
            if aid not in assets:continue
            if q!=balances[aid]:add('REC_QTY','ERROR','계산원장 수량 대사 실패','계산 엔진 수량과 거래원장 수량이 다릅니다.',aid,balances[aid],q)
            if all(x.is_finite() for x in (book,tax,reserve)) and abs((tax-book)-reserve)>Decimal('1'):add('REC_RESERVE','ERROR','회계·세무 장부금액 대사 실패','세무상 장부금액 − 회계상 장부금액이 계산 유보잔액과 다릅니다.',aid,tax-book,reserve)
            if q==0 and any(abs(x)>Decimal('1') for x in (book,tax,reserve) if x.is_finite()):add('REC_CLOSED','ERROR','전량 처분 잔액 오류','보유수량이 0인데 장부금액이나 유보잔액이 남아 있습니다.',aid)
        # Manual reserve is not necessarily equivalent to computed temporary differences.
        manual=state.get('adjustments',[])
        for aid in assets:
            previous=sum((D(x.get('reserve')) for x in manual if str(x.get('assetId'))==aid and x.get('date','')<=end.isoformat()),Decimal(0))
            if manual and any(str(x.get('assetId'))==aid for x in manual):
                pp=next((x for x in pos if str(x.get('assetId'))==aid),None)
                if pp and abs(previous-D(pp.get('reserve')))>Decimal('1'):
                    add('RES_MANUAL_RECON','WARNING','수동 유보와 자동 계산 차이','수동 조정 누계와 자동 장부금액 차이를 검토해야 합니다. FVOCI의 OCI 관련 조정은 별도 검토 대상입니다.',aid,pp.get('reserve'),previous)
        if calculated.get('prior_reserves') is not None:
            for aid, prior in calculated['prior_reserves'].items():
                opening=(calculated.get('opening_reserves') or {}).get(aid)
                if opening is not None and abs(D(prior)-D(opening))>Decimal('1'):
                    add('RES_CARRYFORWARD','ERROR','전기 유보 이월 불일치','전기 기말과 당기 기초 유보잔액이 다릅니다.',aid,prior,opening)
        else:add('RES_CARRYFORWARD_UNVERIFIED','WARNING','전기 유보 이월 검증 미완료','독립된 전기 확정잔액·당기 기초잔액이 없어 완전한 이월 대사는 보류합니다.',year)
    return {'year':year,'year_end':end.isoformat(),'issues':issues,'summary':{s:sum(i['severity']==s for i in issues) for s in ('ERROR','WARNING','INFO')},'scope':'일반 국내 상장주식 검증용; 신고 적합성은 별도 판단'}
