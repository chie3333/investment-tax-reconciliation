/* Shared pure calculation module; no DOM, storage or network access. */
(function(root,factory){
 const api=factory();
 if(typeof module==='object'&&module.exports)module.exports=api;
 else root.InvestmentEngine=api;
})(globalThis,function(){
'use strict';
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
// BigInt rationals parse the decimal input text before any monetary arithmetic.
const gcd=(a,b)=>{a=a<0n?-a:a;b=b<0n?-b:b;while(b){[a,b]=[b,a%b]}return a||1n};
class Rational{
 constructor(n=0n,d=1n){if(d===0n)throw Error('0으로 나눌 수 없습니다.');if(d<0n){n=-n;d=-d}const g=gcd(n,d);this.n=n/g;this.d=d/g;}
 static from(value){
  if(value instanceof Rational)return value;
  if(typeof value==='boolean'||value==null)throw Error('수량·금액 형식 오류');
  const m=String(value).match(/^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/);
  if(!m||String(value).length>128)throw Error('수량·금액 형식 오류');
  const exp=Number(m[4]||0)-(m[3]||'').length;
  if(Math.abs(exp)>300)throw Error('금액 범위 오류');
  const n=BigInt((m[1]==='-'?'-':'')+m[2]+(m[3]||''));
  return exp>=0?new Rational(n*10n**BigInt(exp)):new Rational(n,10n**BigInt(-exp));
 }
 add(v){v=Rational.from(v);return new Rational(this.n*v.d+v.n*this.d,this.d*v.d)}
 sub(v){return this.add(Rational.from(v).neg())}
 mul(v){v=Rational.from(v);return new Rational(this.n*v.n,this.d*v.d)}
 div(v){v=Rational.from(v);return new Rational(this.n*v.d,this.d*v.n)}
 neg(){return new Rational(-this.n,this.d)}
 eq(v){v=Rational.from(v);return this.n===v.n&&this.d===v.d}
 lt(v){v=Rational.from(v);return this.n*v.d<v.n*this.d}
 round(scale=0){const factor=10n**BigInt(scale),n=this.n*factor,abs=n<0n?-n:n;const units=(abs*2n+this.d)/(this.d*2n);return new Rational(n<0n?-units:units,factor)}
 text(scale=0){const r=this.round(scale),factor=10n**BigInt(scale),units=r.n*factor/r.d,abs=units<0n?-units:units;return (units<0n?'-':'')+(abs/factor)+(scale?'.'+String(abs%factor).padStart(scale,'0'):'')}
 number(scale=0){const text=this.text(scale),n=Number(text);if(!Number.isFinite(n)||(scale===0&&!Number.isSafeInteger(n))||!Rational.from(n).eq(this.round(scale)))throw Error('화면의 정확한 금액 표현 범위를 초과했습니다.');return n||0}
}
const R=v=>Rational.from(v),zero=()=>R(0);
function decimalProduct(left,right){
 const value=R(left).mul(right);let denominator=value.d,twos=0,fives=0;
 while(denominator%2n===0n){denominator/=2n;twos++}
 while(denominator%5n===0n){denominator/=5n;fives++}
 if(denominator!==1n)throw Error('십진 입력 곱셈 오류');
 return value.text(Math.max(twos,fives));
}
const portion=(value,balance)=>{let v=R(value).round();const lo=balance.lt(0)?balance:zero(),hi=balance.lt(0)?zero():balance;return v.lt(lo)?lo:hi.lt(v)?hi:v};
function roundingProfileForYear(state,year){
 const p=state.roundingPolicy;
 if(p!==undefined&&p!==null&&(!p||p.profile!=='KRW_WON_V1'||!Number.isInteger(p.effectiveYear)||p.effectiveYear<1900||p.effectiveYear>2200))throw Error('원 단위 반올림 정책·적용 시작 사업연도를 확인하세요.');
 return p&&Number(year)>=p.effectiveYear?'KRW_WON_V1':'LEGACY_CENT_V1';
}
function calculateExact(db,asOf='9999-12-31',options={}){
 const schedule=fvociPolicySchedule(db),month=db.fiscalEndMonth||12,fy=d=>fiscalYear(d,month);
 const profile=y=>roundingProfileForYear(db,y),won=y=>profile(y)==='KRW_WON_V1';
 const pub=(v,year)=>R(v).number(won(year)?0:2),post=(v,year)=>won(year)?R(v).round():R(v);
 const taxMethod=(code,year)=>{
  if(options.taxMethod)return options.taxMethod(code,String(year));
  const p=[...(db.taxPolicyYears||[])].filter(p=>Number(p.effectiveYear)<=Number(year)).sort((a,b)=>Number(b.effectiveYear)-Number(a.effectiveYear))[0]||db.taxPolicy||{};
  return p.filingStatus==='NOT_FILED'?'TOTAL':p.method||'TOTAL';
 };
 const bookMethod=code=>options.accountingMethod?options.accountingMethod(code):db.stockPolicies?.[code]?.book||'TOTAL';
 const balances=new Map(),groups=new Map(),assets=new Map();
 for(const a of db.assets){
  if(!Number.isSafeInteger(a.id)||a.id<=0||assets.has(a.id)||!['FVOCI','FVTPL'].includes(a.classification)||!a.stock_code)throw Error('자산 ID·종목·회계분류 오류');
  assets.set(a.id,a);if(!groups.has(a.stock_code))groups.set(a.stock_code,[]);groups.get(a.stock_code).push(a.id);
  balances.set(a.id,{qty:0,book:zero(),tax:zero(),reserve:zero(),oci:zero(),activeOCI:zero(),pl:zero(),dividend:zero(),exactBook:zero(),exactActiveOCI:zero()});
 }
 const events=[],trades=[],seen=new Set(),lots=new Map();
 for(const [table,kind] of [['trades','trade'],['valuations','valuation']])for(const raw of db[table]||[]){
  const e={...raw,kind},key=kind+':'+e.id;
  if(!Number.isSafeInteger(e.id)||e.id<=0||seen.has(key)||!assets.has(e.assetId))throw Error('중복 원천 ID 또는 미등록 자산');
  seen.add(key);
  if(!/^\d{4}-\d{2}-\d{2}$/.test(e.date)||!Number.isFinite(Date.parse(e.date))||new Date(e.date).toISOString().slice(0,10)!==e.date)throw Error('거래·평가 날짜 오류');
  if(kind==='trade'){
   if(!['BUY','SELL','DIVIDEND'].includes(e.type))throw Error('거래 유형 오류');
   e.amount=R(e.amount);if(e.amount.lt(0)||R('1e15').lt(e.amount))throw Error('거래금액 범위 오류');e.amount=post(e.amount,fy(e.date));
   if(e.type!=='DIVIDEND'){
    if(!Number.isSafeInteger(e.qty)||e.qty<=0)throw Error('양의 정수 수량이 필요합니다.');
    if(Number(fy(e.date))<=Number(fy(asOf)))trades.push(e);
   }
  }else{e.price=R(e.price);if(e.price.lt(0)||R('1e15').lt(e.price))throw Error('평가가격 범위 오류');}
  if(e.date<=asOf)events.push(e);
 }
 trades.sort((a,b)=>a.date.localeCompare(b.date)||a.id-b.id);
 // Keep the existing historical-acquisition-cost explanation, separate from
 // fair-value book removal and the company-wide tax pool.
 const historicalCosts=new Map();
 for(const a of assets.values()){
  const source=trades.filter(e=>e.assetId===a.id),method=bookMethod(a.stock_code),lots=new Map();let qty=0,basis=zero();
  for(const year of [...new Set(source.map(e=>fy(e.date)))].sort()){
   const annual=source.filter(e=>fy(e.date)===year),buys=annual.filter(e=>e.type==='BUY'),totalQty=qty+buys.reduce((q,e)=>q+e.qty,0);
   const unit=totalQty?buys.reduce((v,e)=>v.add(e.amount),basis).div(totalQty):zero();
   for(const e of annual){
    if(e.type==='BUY'){qty+=e.qty;basis=basis.add(e.amount);lots.set(e.id,{qty:e.qty,unit:e.amount.div(e.qty)});continue;}
    if(e.qty>qty)throw Error('자산 보유수량 초과 매도');
    let cost=zero();
    if(method==='TOTAL')cost=unit.mul(e.qty);
    else if(method==='MOVING')cost=basis.div(qty).mul(e.qty);
    else{
     let left=e.qty;
     const allocations=method==='SPECIFIC'?(e.lotAllocation||[]):[...lots].map(([id,lot])=>{const q=Math.min(left,lot.qty);left-=q;return {id,qty:q}});
     for(const allocation of allocations){const lot=lots.get(Number(allocation.id));if(!lot)continue;cost=cost.add(lot.unit.mul(allocation.qty));lot.qty-=Number(allocation.qty);}
    }
    historicalCosts.set(e.id,cost);basis=basis.sub(cost);qty-=e.qty;if(!qty)basis=zero();
   }
   if(method==='TOTAL')basis=unit.mul(qty);
  }
 }
 const costs=new Map();
 for(const [code,ids] of groups){
  const stock=trades.filter(e=>ids.includes(e.assetId));let qty=0,basis=zero(),posted=zero();
  let previousWon=false;
  for(const year of [...new Set(stock.map(e=>fy(e.date)))].sort()){
   if(won(year)&&!previousWon){posted=posted.round(2);if(posted.d!==1n)throw Error('전기 소수 잔액의 원 단위 전환 대사가 필요합니다.');basis=posted;}
   previousWon=won(year);
   const annual=stock.filter(e=>fy(e.date)===year),buys=annual.filter(e=>e.type==='BUY'),method=taxMethod(code,year);
   if(!['TOTAL','MOVING'].includes(method))throw Error('세무원가 정책 오류');
   const totalQty=qty+buys.reduce((q,e)=>q+e.qty,0),totalBasis=buys.reduce((v,e)=>v.add(e.amount),basis),unit=totalQty?totalBasis.div(totalQty):zero();
   if(!Number.isSafeInteger(totalQty))throw Error('수량이 화면의 정확한 정수 표현 범위를 초과했습니다.');
   for(const e of annual){
    if(e.type==='BUY'){qty+=e.qty;basis=basis.add(e.amount);posted=posted.add(e.amount)}
    else{
     if(e.qty>qty)throw Error('종목 통합 보유수량 초과 매도');
     const exact=R(e.qty).mul(method==='TOTAL'?unit:basis.div(qty));
     const future=buys.some(b=>b.date>e.date||(b.date===e.date&&b.id>e.id)),last=e.qty===qty&&(method==='MOVING'||!future);
     const cost=won(year)&&last?posted:won(year)&&(!future||method==='MOVING')?portion(exact,posted):post(exact,year);
     costs.set(e.id,cost);posted=posted.sub(cost);basis=basis.sub(exact);qty-=e.qty;
    }
   }
   if(method==='TOTAL')basis=R(qty).mul(unit);
   if(won(year))basis=posted;
   if(!qty){basis=zero();posted=zero()}
  }
 }
 const stockBasis=new Map([...groups.keys()].map(k=>[k,zero()])),ledger=[];
 let priorYear=null;
 events.sort((a,b)=>a.date.localeCompare(b.date)||(a.kind==='valuation')-(b.kind==='valuation')||a.id-b.id);
 for(const e of events){
  const year=fy(e.date),a=assets.get(e.assetId),b=balances.get(e.assetId),code=a.stock_code,type=e.kind==='trade'?e.type:'VALUATION',policy=resolveFvociPolicy(schedule,year),isWon=won(year);
  if(isWon&&priorYear!==year&&(priorYear===null||!won(priorYear))){
   if([...balances.values()].some(v=>['book','tax','reserve','oci','activeOCI'].some(k=>v[k].d!==1n)))throw Error('전기 소수 잔액의 원 단위 전환 대사가 필요합니다.');
   for(const [stock,ids] of groups)stockBasis.set(stock,ids.reduce((v,id)=>v.add(balances.get(id).tax),zero()));
  }
  priorYear=year;
  let bookPL=zero(),taxPL=zero(),change=zero(),transfer=zero(),removed=zero(),taxCost=zero();
  if(!lots.has(a.id))lots.set(a.id,new Map());const assetLots=lots.get(a.id);
  if(type==='BUY'){
   b.qty+=e.qty;b.book=b.book.add(e.amount);b.exactBook=b.exactBook.add(e.amount);stockBasis.set(code,stockBasis.get(code).add(e.amount));assetLots.set(e.id,e.qty);
  }else if(type==='SELL'){
   if(e.qty>b.qty)throw Error('자산 보유수량 초과 매도');
   const method=bookMethod(code);if(!['TOTAL','MOVING','FIFO','SPECIFIC'].includes(method))throw Error('회계 원가배분 정책 오류');
   if(method==='SPECIFIC'){
    const used=new Map();for(const x of e.lotAllocation||[])used.set(Number(x.id),(used.get(Number(x.id))||0)+Number(x.qty));
    if([...used.values()].reduce((x,y)=>x+y,0)!==e.qty||[...used].some(([id,q])=>!Number.isSafeInteger(q)||q<=0||q>(assetLots.get(id)||0)))throw Error('개별법 LOT 배분 오류');
    for(const [id,q] of used)assetLots.set(id,assetLots.get(id)-q);
   }else if(method==='FIFO'){let left=e.qty;for(const [id,q] of assetLots){const take=Math.min(left,q);assetLots.set(id,q-take);left-=take;}}
   const part=R(e.qty).div(b.qty),exactRemoved=b.exactBook.mul(part);
   removed=isWon?(e.qty===b.qty?b.book:portion(exactRemoved,b.book)):b.book.mul(part);b.exactBook=b.exactBook.sub(exactRemoved);
   taxCost=costs.get(e.id);taxPL=e.amount.sub(taxCost);stockBasis.set(code,stockBasis.get(code).sub(taxCost));
   if(a.classification==='FVOCI'){
    const exactOCI=b.exactActiveOCI.mul(part),disposed=isWon?(e.qty===b.qty?b.activeOCI:portion(exactOCI,b.activeOCI)):b.activeOCI.mul(part);
    b.exactActiveOCI=b.exactActiveOCI.sub(exactOCI);change=e.amount.sub(removed);transfer=policy==='TRANSFER'?disposed.add(change):zero();
    b.activeOCI=b.activeOCI.sub(disposed);b.oci=b.oci.add(change).sub(transfer);
   }else bookPL=e.amount.sub(removed);
   b.qty-=e.qty;b.book=b.book.sub(removed);if(!b.qty)b.book=zero();
  }else if(type==='DIVIDEND'){bookPL=e.amount;taxPL=e.amount;b.dividend=b.dividend.add(e.amount);}
  else{
   if(!b.qty)throw Error('보유수량 없는 평가');
   const target=post(R(b.qty).mul(e.price),year),gain=target.sub(b.book);b.book=target;b.exactBook=target;
   if(a.classification==='FVOCI'){change=gain;b.oci=b.oci.add(gain);b.activeOCI=b.activeOCI.add(gain);b.exactActiveOCI=b.exactActiveOCI.add(gain);}
   else bookPL=gain;
  }
  b.pl=b.pl.add(bookPL);
  const ids=groups.get(code),held=ids.filter(id=>balances.get(id).qty),totalQty=held.reduce((v,id)=>v+balances.get(id).qty,0),scale=isWon?0:2;
  const total=held.length?stockBasis.get(code).round(scale):zero();let assigned=zero();const effects=[];
  for(const id of ids){
   const current=balances.get(id),before=current.reserve;
   let allocated=id===held.at(-1)?total.sub(assigned):current.qty?total.mul(current.qty).div(totalQty).round(scale):zero();
   if(isWon&&!total.lt(0)){if(allocated.lt(0))allocated=zero();if(total.sub(assigned).lt(allocated))allocated=total.sub(assigned);}
   assigned=assigned.add(allocated);
   current.tax=allocated;current.reserve=allocated.sub(current.book.round(scale));const movement=current.reserve.sub(before);
   if(!movement.eq(0)||id===a.id)effects.push({assetId:id,tax:pub(allocated,year),book:pub(current.book,year),reserve:pub(current.reserve,year),reserveChange:pub(movement,year)});
  }
  const own=effects.find(x=>x.assetId===a.id),adj=taxPL.sub(bookPL);
  ledger.push({asset:a,date:e.date,kind:e.kind,type,desc:type==='SELL'?`매도 · 회계 ${{TOTAL:'총평균',MOVING:'이동평균',FIFO:'선입선출',SPECIFIC:'개별'}[bookMethod(code)]} 취득원가 ${pub(historicalCosts.get(e.id),year)}원(참고), 회계 제거장부가 ${pub(removed,year)}원, 세무 처분원가 ${pub(taxCost,year)}원`:type==='BUY'?'매수 · 취득원가 증가':type==='DIVIDEND'?'배당수익':`${a.classification} 평가`,
   qty:b.qty,book:own.book,tax:own.tax,bookPL:pub(bookPL,year),taxPL:pub(taxPL,year),ociChange:pub(change,year),adj:pub(adj,year),reserveChange:own.reserveChange,reserve:own.reserve,
   sourceId:e.id,ociEquityTransfer:pub(transfer,year),directEquityDisposal:0,ociTaxableCandidate:a.classification==='FVOCI'&&type==='SELL'?pub(taxPL,year):0,
   ociAdjustmentReview:a.classification==='FVOCI'&&['SELL','VALUATION'].includes(type),allocationEffects:effects,
   direction:adj.lt(0)?'익금불산입 / 손금산입':adj.eq(0)?'':'익금산입 / 손금불산입',
   ...(a.classification==='FVOCI'?{ociTaxBridge:type==='SELL'?pub(taxPL,year):0,ociTaxBridgeNote:'FVOCI: 법령·소득처분 검토 필요',ociRecycling:false,fvociDisposalPolicy:policy,taxDisposition:'REVIEW_REQUIRED'}:{})});
 }
 const year=fy(asOf),positions=[...assets.values()].map(a=>{
  const b=balances.get(a.id),out={asset:a,qty:b.qty};
  for(const key of ['book','tax','reserve','oci','activeOCI','pl','dividend'])out[key]=pub(b[key],year);
  out.retainedDisposalOCI=pub(b.oci.sub(b.activeOCI),year);out.fvociDisposalPolicy=resolveFvociPolicy(schedule,year);out.taxAllocationMethod='POOLED_STOCK_PRO_RATA';return out;
 });
 const reconciliation=[...groups].map(([code,ids])=>({stock_code:code,qty:ids.reduce((v,id)=>v+balances.get(id).qty,0),
  ...Object.fromEntries(['book','tax','reserve'].map(k=>[k,pub(ids.reduce((v,id)=>v.add(balances.get(id)[k].round(won(year)?0:2)),zero()),year)])),fiscalYear:year}));
 return {positions,ledger,reconciliation};
}
function calculate(db,asOf='9999-12-31',options={}){
 fvociPolicySchedule(db);roundingProfileForYear(db,Number(fiscalYear(asOf,db.fiscalEndMonth||12)));
 try{return calculateExact(db,asOf,options)}
 catch(error){return {positions:[],ledger:[{error:true,desc:error.message,asset:{id:0,asset_name:'계산 오류',stock_code:'',classification:''},date:asOf,type:'ERROR',sourceId:0}],reconciliation:[]}}
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

return Object.freeze({calculate,fiscalYear,fiscalEndDate,workpaperCandidates,fvociPolicyForYear,roundingProfileForYear,roundWon:value=>R(value).number(0),decimalProduct});
});
