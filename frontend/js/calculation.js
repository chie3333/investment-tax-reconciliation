/* Shared pure calculation module; no DOM, storage or network access. */
(function(root,factory){
 const api=factory();
 if(typeof module==='object'&&module.exports)module.exports=api;
 else root.InvestmentEngine=api;
})(globalThis,function(){
'use strict';
const roundW=n=>Math.sign(n)*Math.round((Math.abs(n)+Number.EPSILON*Math.max(1,Math.abs(n))*8)*100)/100||0;
function fiscalYear(date,endMonth=12){const [y,m]=date.slice(0,7).split('-').map(Number);return String(y+(m>Number(endMonth)?1:0));}
function fiscalEndDate(year,endMonth=12){return year+'-'+String(endMonth).padStart(2,'0')+'-'+String(new Date(Number(year),endMonth,0).getDate()).padStart(2,'0');}
const apiFiscalYear=fiscalYear;
function fvociPolicySchedule(db){
 const baseline=db.fvociPolicy?.disposal===undefined?'TRANSFER':db.fvociPolicy.disposal;
 if(!['TRANSFER','RETAIN'].includes(baseline))throw new Error('FVOCI 처분 정책 오류');
 const rows=db.fvociPolicyYears===undefined?[]:db.fvociPolicyYears;
 if(!Array.isArray(rows))throw new Error('FVOCI 사업연도별 정책은 배열이어야 합니다.');
 const seen=new Set();
 for(const row of rows){
  if(!row||!Number.isInteger(row.effectiveYear)||row.effectiveYear<1900||row.effectiveYear>2200||seen.has(row.effectiveYear))throw new Error('FVOCI 적용 시작 사업연도 오류 또는 중복');
  seen.add(row.effectiveYear);
  if(!['TRANSFER','RETAIN'].includes(row.disposal))throw new Error('FVOCI 처분 정책 오류');
  if(!String(row.reviewer||'').trim()||!String(row.reason||'').trim())throw new Error('FVOCI 사업연도별 정책 확인자와 사유가 필요합니다.');
 }
 return {baseline,rows:[...rows].sort((a,b)=>a.effectiveYear-b.effectiveYear)};
}
function resolveFvociPolicy(schedule,year){return schedule.rows.filter(r=>r.effectiveYear<=Number(year)).at(-1)?.disposal||schedule.baseline;}
function fvociPolicyForYear(db,year){return resolveFvociPolicy(fvociPolicySchedule(db),year);}
function calculate(db,asOf='9999-12-31',options={}){
 const policySchedule=fvociPolicySchedule(db);
 const fiscalYear=date=>apiFiscalYear(date,db.fiscalEndMonth||12);
 const selectedAccountingMethod=options.accountingMethod||(code=>{
  const method=db.stockPolicies?.[code]?.book;
  return ['TOTAL','MOVING','FIFO','SPECIFIC'].includes(method)?method:'TOTAL';
 });
 const selectedTaxMethod=options.taxMethod||((code,year)=>{
  const rows=(db.taxPolicyYears||[]).filter(p=>Number(p.effectiveYear)<=Number(year)).sort((a,b)=>Number(b.effectiveYear)-Number(a.effectiveYear));
  const p=rows[0]||db.taxPolicy||{};
  return p.filingStatus==='NOT_FILED'?'TOTAL':(['TOTAL','MOVING'].includes(p.method)?p.method:'TOTAL');
 });

 const ledger=[],positions=[];
 // Annual weighted-average pool: beginning-of-year tax/book cost + all purchases
 // within that calendar year.  For the annual total average, future purchases
 // change the year's earlier sale basis (provisional until year-end).
 function annualPools(events,method){
  const pools=new Map();let carryQ=0,carryCost=0;
  const years=[...new Set(events.map(e=>fiscalYear(e.date)))].sort();
  for(const year of years){
   const yr=events.filter(e=>fiscalYear(e.date)===year);
   const buys=yr.filter(e=>e.kind==='trade'&&e.type==='BUY');
   const sells=yr.filter(e=>e.kind==='trade'&&e.type==='SELL');
   const buyQ=buys.reduce((v,e)=>v+e.qty,0),buyCost=buys.reduce((v,e)=>v+e.amount,0);
   const unit=(carryQ+buyQ)>0?(carryCost+buyCost)/(carryQ+buyQ):0;
   const soldQ=sells.reduce((v,e)=>v+e.qty,0);
   pools.set(year,{unit,openingQ:carryQ,openingCost:carryCost});
   carryQ=carryQ+buyQ-soldQ;
   carryCost=(carryQ>0?carryQ*unit:0);
  }
  return pools;
 }
 // Tax basis is pooled by stock code, independently of accounting classification.
 // Same-date transactions use date and trade ID order; annual TOTAL uses all buys
 // in the calendar year and the carried-forward tax basis from the prior year.
 const pooledTaxCosts=new Map(), pooledTaxErrors=new Map();
 const stockCodes=[...new Set(db.assets.map(a=>a.stock_code))];
 for(const code of stockCodes){
  const ids=new Set(db.assets.filter(a=>a.stock_code===code).map(a=>a.id));
  const trades=db.trades.filter(t=>ids.has(t.assetId)&&['BUY','SELL'].includes(t.type)&&fiscalYear(t.date)<=fiscalYear(asOf))
   .sort((x,y)=>x.date.localeCompare(y.date)||x.id-y.id);
  let openingQty=0,openingCost=0;
  for(const year of [...new Set(trades.map(t=>fiscalYear(t.date)))].sort()){
   const yr=trades.filter(t=>fiscalYear(t.date)===year);
   const method=selectedTaxMethod(code,year);
   if(method==='TOTAL'){
    const buys=yr.filter(t=>t.type==='BUY');
    const totalQty=openingQty+buys.reduce((n,t)=>n+t.qty,0);
    const totalCost=openingCost+buys.reduce((n,t)=>n+t.amount,0);
    const unit=totalQty>0?totalCost/totalQty:0;
    let available=openingQty;
    for(const t of yr){
     if(t.type==='BUY')available+=t.qty;
     else if(t.qty>available+1e-9){pooledTaxErrors.set(t.id,'동일 종목 전체 보유수량을 초과한 처분');}
     else {pooledTaxCosts.set(t.id,t.qty*unit);available-=t.qty;}
    }
    openingQty=available;openingCost=openingQty*unit;
   }else{
    for(const t of yr){
     if(t.type==='BUY'){openingQty+=t.qty;openingCost+=t.amount;}
     else if(t.qty>openingQty+1e-9){pooledTaxErrors.set(t.id,'동일 종목 전체 보유수량을 초과한 처분');}
     else {const cost=openingQty>0?t.qty*openingCost/openingQty:0;pooledTaxCosts.set(t.id,cost);openingQty-=t.qty;openingCost-=cost;if(openingQty===0)openingCost=0;}
    }
   }
  }
 }
 for(const a of db.assets){
  const accountingMethod=selectedAccountingMethod(a.stock_code);
  let q=0,book=0,tax=0,oci=0,activeOCI=0,pl=0,dividend=0,reserve=0;
  const allEvents=[...db.trades.filter(t=>t.assetId===a.id).map(t=>({...t,kind:'trade'})),...db.valuations.filter(v=>v.assetId===a.id).map(v=>({...v,kind:'valuation'}))].sort((x,y)=>x.date.localeCompare(y.date)||(x.kind==='trade'?0:1)-(y.kind==='trade'?0:1)||x.id-y.id);
  const events=allEvents.filter(e=>e.date<=asOf);
  // Include the full available year in annual-average calculation, even when
  // the display is filtered to an earlier date within that year.
  const poolEvents=allEvents.filter(e=>fiscalYear(e.date)<=fiscalYear(asOf));
  const bookPools=accountingMethod==='TOTAL'?annualPools(poolEvents,'TOTAL'):null;
  // Legacy per-asset tax pool (not used for disposal cost); pooledTaxCosts is authoritative.
  // Compute annual tax total-average units with opening basis carried from
  // the preceding year's selected method (including moving-average years).
  const taxPools=new Map();let carryQ=0,carryCost=0;
  for(const yr of [...new Set(poolEvents.map(e=>fiscalYear(e.date)))].sort()){
   const method=selectedTaxMethod(a.stock_code,yr);
   const yrTrades=poolEvents.filter(e=>e.kind==='trade'&&fiscalYear(e.date)===yr).sort((x,y)=>x.date.localeCompare(y.date)||x.id-y.id);
   const buys=yrTrades.filter(e=>e.type==='BUY');
   const buyQ=buys.reduce((v,e)=>v+e.qty,0),buyCost=buys.reduce((v,e)=>v+e.amount,0);
   const unit=(carryQ+buyQ)>0?(carryCost+buyCost)/(carryQ+buyQ):0;
   taxPools.set(yr,{unit,method});
   if(method==='TOTAL'){
    const soldQ=yrTrades.filter(e=>e.type==='SELL').reduce((v,e)=>v+e.qty,0);
    carryQ+=buyQ-soldQ;carryCost=carryQ>0?carryQ*unit:0;
   }else{
    for(const e of yrTrades){if(e.type==='BUY'){carryQ+=e.qty;carryCost+=e.amount}
     if(e.type==='SELL'&&carryQ>0){const c=e.qty*carryCost/carryQ;carryQ-=e.qty;carryCost-=c;if(carryQ===0)carryCost=0;}}
   }
  }
  let taxMovingQ=0,taxMovingCost=0,bookCostQ=0,bookCostBalance=0,bookLots=[];
  for(const e of events){
   const disposalPolicy=resolveFvociPolicy(policySchedule,fiscalYear(e.date));
   let bookPL=0,taxPL=0,adj=0,classification=a.classification,desc='',direction='',reserveChange=0,deltaOCI=0,ociEquityTransfer=0,directEquityDisposal=0,ociTaxableCandidate=0,ociAdjustmentReview=false;
   const oldBook=book,oldTax=tax,year=fiscalYear(e.date),taxMethod=selectedTaxMethod(a.stock_code,year);
   if(e.kind==='trade'&&e.type==='BUY'){
    if(!(e.qty>0)||e.amount<0){ledger.push({asset:a,date:e.date,desc:'입력 오류: 매수',error:true});continue}
    q+=e.qty;book+=e.amount;tax+=e.amount;
    bookCostQ+=e.qty;bookCostBalance+=e.amount;bookLots.push({id:e.id,qty:e.qty,unit:e.amount/e.qty});
    taxMovingQ+=e.qty;taxMovingCost+=e.amount;
    desc='매수 · 취득원가 증가';
   }else if(e.kind==='trade'&&e.type==='SELL'){
    if(!(e.qty>0)||e.qty>q){ledger.push({asset:a,date:e.date,desc:'오류: 보유수량 초과 매도',error:true});continue}
    const proceeds=e.amount;
    let accountingHistoricalCost=0;
    if(accountingMethod==='TOTAL') accountingHistoricalCost=e.qty*(bookPools.get(year)?.unit||0);
    else if(accountingMethod==='MOVING') accountingHistoricalCost=e.qty*(bookCostQ>0?bookCostBalance/bookCostQ:0);
    else {
     let remaining=e.qty;
     let allocation=[];
     if(accountingMethod==='SPECIFIC'){
      allocation=Array.isArray(e.lotAllocation)?e.lotAllocation:[];
      if(!allocation.length || Math.abs(allocation.reduce((v,x)=>v+Number(x.qty||0),0)-e.qty)>0.000001){ledger.push({asset:a,date:e.date,desc:'개별법: 매도 거래의 취득 LOT 배분이 누락되거나 수량이 일치하지 않습니다.',error:true});continue}
     }else allocation=bookLots.map(l=>({id:l.id,qty:Math.min(remaining,Math.max(0,l.qty))})).filter(x=>{remaining-=x.qty;return x.qty>0});
     if(allocation.some(x=>{const lot=bookLots.find(l=>l.id===Number(x.id));return !lot||Number(x.qty)<=0||Number(x.qty)>lot.qty;})){ledger.push({asset:a,date:e.date,desc:'개별법: 유효하지 않은 취득 LOT 배분입니다.',error:true});continue}
     for(const x of allocation){const lot=bookLots.find(l=>l.id===Number(x.id));accountingHistoricalCost+=Number(x.qty)*lot.unit;lot.qty-=Number(x.qty)}
    }
    // The fair-value carrying amount removed is the fraction of the position
    // being derecognized; this differs from historical acquisition cost.
    const bookRemoved=book*(e.qty/q);
    if(pooledTaxErrors.has(e.id)||!pooledTaxCosts.has(e.id)){ledger.push({asset:a,date:e.date,desc:'세법상 통합 원가계산 오류: '+(pooledTaxErrors.get(e.id)||'처분원가 없음'),error:true});continue}
    const taxCost=pooledTaxCosts.get(e.id);
    book-=bookRemoved;tax-=taxCost;q-=e.qty;
    bookCostQ-=e.qty;bookCostBalance-=accountingHistoricalCost;
    taxMovingQ-=e.qty;taxMovingCost-=taxCost;
    if(q===0){book=0;tax=0;bookCostQ=0;bookCostBalance=0;taxMovingQ=0;taxMovingCost=0}
    taxPL=proceeds-taxCost;
    if(classification==='FVTPL')bookPL=proceeds-bookRemoved;
    else {
     // Remeasure the disposed shares to proceeds (assumed fair value, no fees).
     // Include that final change in OCI before the within-equity transfer.
     // Remaining shares retain their last explicitly supplied valuation.
     const qtyBefore=q+e.qty;
     const accumulatedOCIShare=qtyBefore>0?activeOCI*e.qty/qtyBefore:0;
     deltaOCI=proceeds-bookRemoved;
     ociEquityTransfer=disposalPolicy==='TRANSFER'?accumulatedOCIShare+deltaOCI:0;
     directEquityDisposal=0;
     oci+=deltaOCI-ociEquityTransfer;
     activeOCI-=accumulatedOCIShare;
     bookPL=0;
     ociTaxableCandidate=taxPL;
     ociAdjustmentReview=true;
    }
    // P/L-based taxable income bridge is not the same as reserve movement.
    // FVOCI disposal's tax profit is a candidate taxable-income adjustment,
    // subject to separate legal review and income-disposition approval.
    adj=taxPL-bookPL;
    reserveChange=(tax-book)-(oldTax-oldBook);
    desc=`매도 · 회계 ${ {TOTAL:"총평균",MOVING:"이동평균",FIFO:"선입선출",SPECIFIC:"개별"}[accountingMethod]} 취득원가 ${roundW(accountingHistoricalCost)}원(참고), 회계 제거장부가 ${roundW(bookRemoved)}원, 세무 ${taxMethod==='TOTAL'?'총평균':'이동평균'} 처분원가 ${roundW(taxCost)}원`;
   }else if(e.kind==='trade'&&e.type==='DIVIDEND'){
    bookPL=e.amount;taxPL=e.amount;dividend+=e.amount;desc='배당수익 · 수입배당금 익금불산입 미적용';
   }else if(e.kind==='valuation'){
    const fair=q*e.price;
    if(q===0){ledger.push({asset:a,date:e.date,desc:'오류: 보유수량 없는 평가',error:true});continue}
    const gain=fair-book;book=fair;
    if(classification==='FVTPL'){bookPL=gain;adj=-gain;desc='FVTPL 평가손익 · 세법상 평가 미인식 가정'}
    else {deltaOCI=gain;oci+=gain;activeOCI+=gain;ociAdjustmentReview=true;desc='FVOCI 평가 · OCI 인식. 과세소득 조정·소득처분은 별도 검토'}
    reserveChange=(tax-book)-(oldTax-oldBook);
   }
   if(adj>0.005)direction='익금산입 / 손금불산입';else if(adj<-.005)direction='익금불산입 / 손금산입';
   pl+=bookPL;reserve+=reserveChange;
   ledger.push({asset:a,date:e.date,kind:e.kind,type:e.type||'VALUATION',desc,qty:q,book:roundW(book),tax:roundW(tax),bookPL:roundW(bookPL),taxPL:roundW(taxPL),ociChange:roundW(deltaOCI),adj:roundW(adj),reserveChange:roundW(reserveChange),reserve:roundW(reserve),direction,sourceId:e.id,ociEquityTransfer:roundW(ociEquityTransfer),directEquityDisposal:roundW(directEquityDisposal),ociTaxableCandidate:roundW(ociTaxableCandidate),ociAdjustmentReview});
  }
  positions.push({asset:a,qty:q,book:roundW(book),tax:roundW(tax),oci:roundW(oci),activeOCI:roundW(activeOCI),retainedDisposalOCI:roundW(oci-activeOCI),fvociDisposalPolicy:resolveFvociPolicy(policySchedule,fiscalYear(asOf)),pl:roundW(pl),dividend:roundW(dividend),reserve:roundW(reserve)});
 }
 // Allocate at every successful event, rather than only at the final snapshot.
 // Allocation effects on other classifications are explicit, never tax profit.
 ledger.sort((a,b)=>a.date.localeCompare(b.date)||(a.kind==='trade'?0:1)-(b.kind==='trade'?0:1)||a.sourceId-b.sourceId||a.asset.id-b.asset.id);
 const balances=new Map(positions.map(p=>[p.asset.id,{qty:0,book:0,tax:0,reserve:0}]));
 const basis=new Map(stockCodes.map(code=>[code,0]));
 for(const row of ledger){
  if(row.error)continue;
  const code=row.asset.stock_code, state=balances.get(row.asset.id);
  const source=(row.kind==='trade'?db.trades:db.valuations).find(e=>e.id===row.sourceId&&e.assetId===row.asset.id);
  if(row.type==='BUY')basis.set(code,basis.get(code)+source.amount);
  if(row.type==='SELL')basis.set(code,basis.get(code)-pooledTaxCosts.get(row.sourceId));
  state.qty=row.qty;state.book=row.book;
  const group=positions.filter(p=>p.asset.stock_code===code);
  const held=group.filter(p=>balances.get(p.asset.id).qty>0);
  const totalQty=held.reduce((n,p)=>n+balances.get(p.asset.id).qty,0);
  const totalBasis=roundW(totalQty?basis.get(code):0);
  let assigned=0;
  row.allocationEffects=[];
  for(const p of group){
   const current=balances.get(p.asset.id),before=current.reserve;
   const last=held.length&&p===held[held.length-1];
   const allocated=!current.qty?0:last?roundW(totalBasis-assigned):roundW(totalBasis*current.qty/totalQty);
   assigned=roundW(assigned+allocated);
   current.tax=allocated;current.reserve=roundW(allocated-current.book);
   const movement=roundW(current.reserve-before);
   if(movement||p.asset.id===row.asset.id)row.allocationEffects.push({assetId:p.asset.id,tax:allocated,book:current.book,reserve:current.reserve,reserveChange:movement});
  }
  const current=balances.get(row.asset.id);
  row.tax=current.tax;row.reserve=current.reserve;
  row.reserveChange=row.allocationEffects.find(e=>e.assetId===row.asset.id).reserveChange;
 }
 for(const p of positions){
  const current=balances.get(p.asset.id);
  p.tax=current.tax;p.reserve=current.reserve;
  p.taxAllocationMethod='POOLED_STOCK_PRO_RATA';
 }
 // Taxable-income adjustment, OCI changes, and reserve rollforward are
 // separate dimensions. Never automatically label a bridge as '기타' or '유보'.
 for(const row of ledger){
  if(row.asset.classification!=='FVOCI'||row.error)continue;
  row.ociTaxBridge=row.type==='SELL'?row.taxPL:0;
  row.ociTaxBridgeNote=row.type==='SELL'?'FVOCI 처분: 세법상 처분손익의 과세소득 반영·소득처분 검토 필요':
    row.type==='VALUATION'?'FVOCI 평가: OCI 변동은 당기손익 아님. 자본·세무기준 차이 검토 필요':'';
  row.ociRecycling=false;
  row.fvociDisposalPolicy=resolveFvociPolicy(policySchedule,fiscalYear(row.date));
  row.taxDisposition='REVIEW_REQUIRED';
 }
 const reconciliation=stockCodes.map(code=>{
  const ps=positions.filter(p=>p.asset.stock_code===code);
  return {stock_code:code,qty:roundW(ps.reduce((n,p)=>n+p.qty,0)),book:roundW(ps.reduce((n,p)=>n+p.book,0)),tax:roundW(ps.reduce((n,p)=>n+p.tax,0)),reserve:roundW(ps.reduce((n,p)=>n+p.reserve,0)),fiscalYear:fiscalYear(asOf)};
 });
 return {positions,ledger,reconciliation};
}

function workpaperCandidates(state,year){
 const result=calculate(state,fiscalEndDate(year,state.fiscalEndMonth||12));
 if(result.ledger.some(r=>r.error))throw new Error('계산 오류가 있어 검증표를 생성할 수 없습니다. 원천 거래를 확인하세요.');
 return result.ledger.filter(r=>fiscalYear(r.date,state.fiscalEndMonth||12)===String(year)&&
  (['SELL','VALUATION'].includes(r.type)||(r.allocationEffects||[]).some(e=>Math.abs(e.reserveChange)>0))).map(r=>({
   asset_id:r.asset.id,stock_code:r.asset.stock_code,asset_name:r.asset.asset_name,
   classification:r.asset.classification,source_type:r.kind,source_id:r.sourceId,date:r.date,event:r.type,
   book_pl:r.bookPL,tax_pl:r.taxPL,oci_change:r.ociChange,oci_equity_transfer:r.ociEquityTransfer||0,
   fvoci_disposal_policy:r.fvociDisposalPolicy||null,allocation_effects:r.allocationEffects||[],
   candidate_adjustment:r.adj,reserve_change:r.reserveChange,tax_basis:r.tax,book_basis:r.book,
   note:r.desc,recognition:r.asset.classification==='FVOCI'?'OCI':'PL'
  }));
}

return Object.freeze({calculate,fiscalYear,fiscalEndDate,workpaperCandidates,fvociPolicyForYear});
});
