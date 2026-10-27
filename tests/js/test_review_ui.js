const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const model=require('../../frontend/js/review-model.js'),elements=new Map(),messages=[],requests=[];
const $=id=>{if(!elements.has(id))elements.set(id,{value:'',checked:false,innerHTML:'',textContent:'',scrollIntoView(){}});return elements.get(id)};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const asset={id:1,asset_name:'<img src=x onerror=alert(1)>',stock_code:'005930',classification:'FVOCI'};
const candidate={asset_id:1,asset_name:asset.asset_name,stock_code:asset.stock_code,classification:'FVOCI',source_type:'trade',source_id:2,event:'SELL',date:'2026-07-01',
 candidate_adjustment:'40',reserve_change:'20',book_basis:'70',tax_basis:'50',book_pl:'0',tax_pl:'40',oci_change:'20',oci_equity_transfer:'40',fvoci_disposal_policy:'TRANSFER',
 review_detail:{quantity:5,quantity_before:10,quantity_after:5,gross_amount:'90',tax_cost:'50',book_removed:'70',disposed_prior_oci:'20',reserve_before:'-40',reserve_after:'-20'},
 allocation_effects:[{assetId:1,reserveChange:'20',book:'70',tax:'50'}],adjustment_plan:{rounding_profile:'KRW_WON_V1',status:'REVIEW_REQUIRED',rows:[
 {asset_id:'1',label:'자본 연결',direction:'익금산입',disposition:'기타',amount:'20',reserve_movement:'0',legal_basis:'근거1'},
 {asset_id:'1',label:'평가 부인',direction:'손금산입',disposition:'△유보',amount:'20',reserve_movement:'-20',legal_basis:'근거2'},
 {asset_id:'1',label:'처분 환입',direction:'익금산입',disposition:'유보',amount:'40',reserve_movement:'40',legal_basis:'근거3'}]}};
let wp={id:1,status:'DRAFT',source_revision:1,candidate,checks:[]},fail=false,ending='-40';
const ctx=vm.createContext({InvestmentReview:model,$,esc,db:{assets:[asset]},dbReady:true,dbRevision:1,dbSaveFailed:false,dbSaveQueue:Promise.resolve(),
 window:{addEventListener(){}},confirm:()=>true,notify:s=>messages.push(s),getAsset:()=>asset,assetLabel:a=>a.asset_name,
 table:(heads,rows)=>'<table>'+heads.join('|')+rows.flat().join('|')+'</table>',fiscalEndDate:()=> '2026-12-31',
 calculation:()=>({positions:[{asset,qty:5,book:70,tax:50}]}),reserveItemLabel:()=> '검토용 주식 · 평가 관련',
 InvestmentEngine:{workpaperCandidates:()=>[candidate]},
 dbRequest:async(url,opts)=>{
  if(opts){requests.push(JSON.parse(opts.body));if(fail)throw Error('서버 거절 테스트');wp={...wp,status:'APPROVED',approved:{adjustments:JSON.parse(opts.body).adjustments},reviewer:'담당자'};ending='-20';return {reserve_posted:2};}
  if(url.includes('/workpapers?'))return {items:[wp]};
  if(url.includes('/closing/summary'))return {year:2026,close:null,opening:{},movements:{'1|FVOCI_VALUATION':ending},ending:{'1|FVOCI_VALUATION':ending},prior_closed:false};
  throw Error('unexpected route '+url);
 }});
new vm.Script(fs.readFileSync('frontend/js/review.js','utf8')).runInContext(ctx);
$('validateYear').value='2026';$('reviewFilter').value='ACTIVE';
(async()=>{
 await ctx.refreshReviewUI();assert.ok($('workpaperList').innerHTML.includes('&lt;img'));
 assert.ok(!$('workpaperList').innerHTML.includes('trade:2:1'));assert.ok($('workpaperList').innerHTML.includes('승인 대기'));
 assert.ok($('closingResult').innerHTML.includes('승인 대기 차이'));
 ctx.showWorkpaperUI(1);assert.ok($('workpaperDetail').innerHTML.includes('처분 수량'));
 assert.ok($('workpaperDetail').innerHTML.includes('90원'));assert.ok($('workpaperDetail').innerHTML.includes('50원'));assert.ok($('workpaperDetail').innerHTML.includes('70원'));
 assert.ok(!$('workpaperDetail').innerHTML.includes('JSON'));assert.ok(!$('reviewRowsHost').innerHTML.includes('<img'));
 ctx.updateReviewRow(2,'amount','41');assert.equal(vm.runInContext('reviewEditor.rows[2].reserve_movement',ctx),'41');
 $('wpReviewer').value='담당자';$('wpNote').value='각 조정 원인과 유보 증감을 확인했습니다.';$('wpAcknowledge').checked=true;
 await ctx.approveWorkpaperUI(1);assert.equal(requests.length,0,'wrong sums must not submit');
 ctx.updateReviewRow(2,'amount','40');ctx.addReviewRow();assert.equal(vm.runInContext('reviewEditor.rows.length',ctx),4);ctx.removeReviewRow(3);
 fail=true;await ctx.approveWorkpaperUI(1);assert.equal(vm.runInContext('reviewEditor.dirty',ctx),true);assert.equal(vm.runInContext('reviewEditor.rows.length',ctx),3);
 assert.ok(messages.some(x=>x.includes('승인 실패: 서버 거절')));
 fail=false;await ctx.approveWorkpaperUI(1);
 assert.equal(requests.at(-1).adjustments.length,3);assert.equal(requests.at(-1).adjustments[2].reserve_movement,'40');
 assert.ok($('workpaperDetail').innerHTML.includes('실제 승인된 조정 행'));assert.ok(!$('reviewRowsHost').innerHTML.includes('<input'));
 assert.ok($('closingResult').innerHTML.includes('일치'));
 // Historical workpaper without detail must never borrow current source figures.
 wp={...wp,candidate:{...candidate,review_detail:undefined}};await ctx.refreshReviewUI();ctx.showWorkpaperUI(1);
 assert.ok($('workpaperDetail').innerHTML.includes('현재 원천으로 과거 승인 내역을 다시 만들지 않습니다'));
 console.log('PASS: real table edit/submit/read-only flow, exact totals, failure retention, source explanations and escaped labels');
})().catch(e=>{console.error(e);process.exitCode=1});
