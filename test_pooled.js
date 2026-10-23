const assert=require('node:assert/strict');
const roundW=n=>Math.round(n*100)/100;
const db={assets:[{id:1,stock_code:'005930',classification:'FVOCI',asset_name:'A'},{id:2,stock_code:'005930',classification:'FVTPL',asset_name:'B'}],trades:[
{id:1,assetId:1,date:'2026-01-01',type:'BUY',qty:10,amount:100},
{id:2,assetId:2,date:'2026-02-01',type:'BUY',qty:10,amount:300},
{id:3,assetId:1,date:'2026-03-01',type:'SELL',qty:5,amount:150},
{id:4,assetId:2,date:'2026-04-01',type:'SELL',qty:5,amount:150},
],valuations:[]};
const selectedAccountingMethod=()=> 'MOVING';
let policy='TOTAL';const selectedTaxMethod=()=>policy;
function calculation(asOf='9999-12-31'){
 const ledger=[],positions=[];
 // Annual weighted-average pool: beginning-of-year tax/book cost + all purchases
 // within that calendar year.  For the annual total average, future purchases
 // change the year's earlier sale basis (provisional until year-end).
 function annualPools(events,method){
  const pools=new Map();let carryQ=0,carryCost=0;
  const years=[...new Set(events.map(e=>e.date.slice(0,4)))].sort();
  for(const year of years){
   const yr=events.filter(e=>e.date.startsWith(year));
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
  const trades=db.trades.filter(t=>ids.has(t.assetId)&&['BUY','SELL'].includes(t.type)&&t.date.slice(0,4)<=asOf.slice(0,4))
   .sort((x,y)=>x.date.localeCompare(y.date)||x.id-y.id);
  let openingQty=0,openingCost=0;
  for(const year of [...new Set(trades.map(t=>t.date.slice(0,4)))].sort()){
   const yr=trades.filter(t=>t.date.startsWith(year));
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
  let q=0,book=0,tax=0,oci=0,pl=0,dividend=0,reserve=0;
  const allEvents=[...db.trades.filter(t=>t.assetId===a.id).map(t=>({...t,kind:'trade'})),...db.valuations.filter(v=>v.assetId===a.id).map(v=>({...v,kind:'valuation'}))].sort((x,y)=>x.date.localeCompare(y.date)||(x.kind==='trade'?0:1)-(y.kind==='trade'?0:1)||x.id-y.id);
  const events=allEvents.filter(e=>e.date<=asOf);
  // Include the full available year in annual-average calculation, even when
  // the display is filtered to an earlier date within that year.
  const poolEvents=allEvents.filter(e=>e.date.slice(0,4)<=asOf.slice(0,4));
  const bookPools=accountingMethod==='TOTAL'?annualPools(poolEvents,'TOTAL'):null;
  // Legacy per-asset tax pool (not used for disposal cost); pooledTaxCosts is authoritative.
  // Compute annual tax total-average units with opening basis carried from
  // the preceding year's selected method (including moving-average years).
  const taxPools=new Map();let carryQ=0,carryCost=0;
  for(const yr of [...new Set(poolEvents.map(e=>e.date.slice(0,4)))].sort()){
   const method=selectedTaxMethod(a.stock_code,yr);
   const yrTrades=poolEvents.filter(e=>e.kind==='trade'&&e.date.startsWith(yr)).sort((x,y)=>x.date.localeCompare(y.date)||x.id-y.id);
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
   let bookPL=0,taxPL=0,adj=0,classification=a.classification,desc='',direction='',reserveChange=0,deltaOCI=0;
   const oldBook=book,oldTax=tax,year=e.date.slice(0,4),taxMethod=selectedTaxMethod(a.stock_code,year);
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
    else {deltaOCI=proceeds-bookRemoved;oci+=deltaOCI;bookPL=0}
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
    else {deltaOCI=gain;oci+=gain;desc='FVOCI 평가 · OCI 인식 (당기손익 세무조정 아님)'}
    reserveChange=(tax-book)-(oldTax-oldBook);
   }
   if(adj>0.005)direction='익금산입 / 손금불산입';else if(adj<-.005)direction='익금불산입 / 손금산입';
   pl+=bookPL;reserve+=reserveChange;
   ledger.push({asset:a,date:e.date,kind:e.kind,type:e.type||'VALUATION',desc,qty:q,book:roundW(book),tax:roundW(tax),bookPL:roundW(bookPL),taxPL:roundW(taxPL),ociChange:roundW(deltaOCI),adj:roundW(adj),reserveChange:roundW(reserveChange),reserve:roundW(reserve),direction,sourceId:e.id});
  }
  positions.push({asset:a,qty:q,book:roundW(book),tax:roundW(tax),oci:roundW(oci),pl:roundW(pl),dividend:roundW(dividend),reserve:roundW(reserve)});
 }
 ledger.sort((a,b)=>a.date.localeCompare(b.date)||a.asset.id-b.asset.id);
 return {positions,ledger};
}

let result=calculation();
assert.equal(result.ledger.find(x=>x.sourceId===3).taxPL,50);
assert.equal(result.ledger.find(x=>x.sourceId===4).taxPL,50);
assert.equal(result.positions.reduce((n,p)=>n+p.tax,0),200);
policy='MOVING';result=calculation();
assert.equal(result.ledger.find(x=>x.sourceId===3).taxPL,50);
assert.equal(result.ledger.find(x=>x.sourceId===4).taxPL,50);
console.log('PASS: pooled cost across FVOCI/FVTPL, total and moving');
