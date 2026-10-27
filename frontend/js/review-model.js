/* Exact presentation arithmetic and review state, independent of DOM/network. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.InvestmentReview=api;})(globalThis,function(){
'use strict';
const statuses={DRAFT:'승인 대기',APPROVED:'승인 완료',CANCELLED:'승인 취소',SUPERSEDED:'이전 회차'};
const events={BUY:'매수',SELL:'처분',VALUATION:'평가',DIVIDEND:'배당'};
function scaleOf(candidate){return candidate.adjustment_plan?.rounding_profile==='KRW_WON_V1'||candidate.review_detail?.rounding_profile==='KRW_WON_V1'?0:2}
function units(value,scale=2){
 const m=String(value??'').trim().match(/^([+-]?)(\d+)(?:\.(\d+))?$/);
 if(!m||m[2].length>18||(m[3]||'').length>64)throw Error('유효한 숫자를 입력하세요.');
 const fraction=m[3]||'';if(/[1-9]/.test(fraction.slice(scale)))throw Error(scale===0?'원 단위 금액을 입력하세요.':'금액은 소수 둘째 자리까지 입력하세요.');
 const n=BigInt(m[2])*10n**BigInt(scale)+BigInt((fraction.slice(0,scale).padEnd(scale,'0'))||'0');
 return m[1]==='-'?-n:n;
}
function decimal(n,scale=2){const sign=n<0n?'-':'',abs=n<0n?-n:n,factor=10n**BigInt(scale);return sign+String(abs/factor)+(scale?'.'+String(abs%factor).padStart(scale,'0'):'')}
function display(value,scale=2){const n=units(value,scale),abs=n<0n?-n:n,factor=10n**BigInt(scale),fraction=scale?String(abs%factor).padStart(scale,'0').replace(/0+$/,''):'';return (n<0n?'−':'')+(abs/factor).toLocaleString('ko-KR')+(fraction?'.'+fraction:'')+'원'}
function movement(row){const amount=String(row.amount??'').trim();return row.disposition==='유보'?amount:row.disposition==='△유보'?'-'+amount.replace(/^\+?/,''):'0'}
function totals(rows,candidate){
 const scale=scaleOf(candidate);let income=0n,reserve=0n;const targets=new Map(),errors=[];
 for(const [index,r] of rows.entries()){
  try{
   const amount=units(r.amount,scale),change=units(r.reserve_movement??0,scale);
   if(amount<=0n||amount>1000000000000000n*10n**BigInt(scale))throw Error('금액은 0원 초과, 1,000조 원 이하로 입력하세요.');
   if(!['익금산입','손금불산입','익금불산입','손금산입'].includes(r.direction))throw Error('조정 방향을 선택하세요.');
   if(!['유보','△유보','기타','기타사외유출','상여','배당'].includes(r.disposition))throw Error('소득처분을 선택하세요.');
   if(change!==units(movement(r),scale))throw Error('소득처분과 유보 증감액이 다릅니다.');
   if(!String(r.legal_basis||'').trim())throw Error('검토 근거를 입력하세요.');
   const id=String(r.asset_id||candidate.asset_id);
   income+=['익금산입','손금불산입'].includes(r.direction)?amount:-amount;reserve+=change;targets.set(id,(targets.get(id)||0n)+change);
  }catch(error){errors.push(`${index+1}행: ${error.message}`)}
 }
 const expected=new Map((candidate.allocation_effects||[]).map(e=>[String(e.assetId),units(e.reserveChange,scale)]));
 const incomeMatch=income===units(candidate.candidate_adjustment,scale);
 const reserveMatch=[...new Set([...targets.keys(),...expected.keys()])].every(id=>expected.has(id)&&(targets.get(id)||0n)===expected.get(id));
 return {scale,income:decimal(income,scale),reserve:decimal(reserve,scale),incomeMatch,reserveMatch,errors};
}
function approvedRows(w){return ['APPROVED','CANCELLED'].includes(w.status)?w.approved?.adjustments??null:null}
function rowsFor(w){const approved=approvedRows(w);return approved!==null?approved.map(r=>({...r})):w.candidate.adjustment_plan?.status==='REVIEW_REQUIRED'?(w.candidate.adjustment_plan.rows||[]).map(r=>({...r})):[]}
function itemLabel(w){const a=w.candidate,d=a.review_detail;return (a.asset_name||a.stock_code||'종목 미확인')+' · '+a.classification+' · '+(a.event==='SELL'&&d&&d.quantity_after>0?'부분 처분':events[a.event]||'거래')}
function reconciliation(actual,expected,pending,complete=true){
 const a=units(actual),e=units(expected),p=units(pending);
 return a===e?'일치':complete&&p===e-a?'승인 대기 차이':'원장 차이 · 원인 확인';
}
return Object.freeze({statuses,events,scaleOf,units,decimal,display,movement,totals,rowsFor,itemLabel,reconciliation});
});
