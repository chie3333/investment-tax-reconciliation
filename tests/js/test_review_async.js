/* Deferred network responses exercise real UI handlers, not a copied implementation. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const model=require('../../frontend/js/review-model.js');
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
function fixture(){
 const elements=new Map(),messages=[],requests=[];
 const $=id=>{if(!elements.has(id))elements.set(id,{value:'',checked:false,innerHTML:'',textContent:'',scrollIntoView(){}});return elements.get(id)};
 const candidate={asset_id:1,asset_name:'예시 주식',stock_code:'A',classification:'FVOCI',event:'SELL',date:'2026-06-01',source_type:'trade',source_id:1,candidate_adjustment:'10',reserve_change:'0',allocation_effects:[{assetId:1,reserveChange:'0'}],adjustment_plan:{status:'REVIEW_REQUIRED',rounding_profile:'KRW_WON_V1',rows:[{asset_id:'1',direction:'익금산입',disposition:'기타',amount:'10',reserve_movement:'0',legal_basis:'검토 근거',label:'처분 조정'}]}};
 const papers=[1,2].map(id=>({id,status:'DRAFT',source_revision:1,candidate:{...candidate,source_id:id}}));
 const approval=deferred(),cancellation=deferred(),reads=deferred();let holdReads=false;
 const ctx=vm.createContext({InvestmentReview:model,$,window:{addEventListener(){}},dbReady:true,dbRevision:1,dbSaveFailed:false,dbSaveQueue:Promise.resolve(),lastValidationRun:1,db:{assets:[{id:1,asset_name:'예시 주식'}]},esc:s=>String(s??''),notify:s=>messages.push(s),confirm:()=>true,prompt:()=>'취소 근거를 충분히 확인했습니다.',getAsset:()=>({asset_name:'예시 주식'}),assetLabel:a=>a.asset_name,table:(heads,rows)=>rows.flat().join('|'),fiscalEndDate:()=> '2026-12-31',calculation:()=>({positions:[]}),InvestmentEngine:{workpaperCandidates:()=>[]},reserveItemLabel:x=>x,loadIssueReviews:async()=>{},
 dbRequest:async(url,options)=>{
  if(options){const body=JSON.parse(options.body);requests.push({url,body});if(url.endsWith('/cancel')){await cancellation.promise;return {reversed_entries:1}}if(url.endsWith('/approve')){await approval.promise;papers[0]={...papers[0],status:'APPROVED',approved:{adjustments:body.adjustments}};return {reserve_posted:0}}return {};}
  if(holdReads)await reads.promise;
  return url.includes('/workpapers?')?{items:papers}:{year:Number($('validateYear').value),close:null,opening:{},movements:{},ending:{}};
 }});
 new vm.Script(fs.readFileSync('frontend/js/review.js','utf8')).runInContext(ctx);
 $('validateYear').value='2026';$('reviewFilter').value='ACTIVE';
 async function start(){await ctx.refreshReviewUI();ctx.showWorkpaperUI(1);ctx.updateReviewApproval('reviewer','검토자');ctx.updateReviewApproval('note','법령과 처분 방향을 확인했습니다.');ctx.updateReviewApproval('acknowledge',true);$('wpReviewer').value='검토자';$('wpNote').value='법령과 처분 방향을 확인했습니다.';$('wpAcknowledge').checked=true;return {pending:ctx.approveWorkpaperUI(1)}}
 return {ctx,$,messages,requests,approval,cancellation,reads,papers,start,holdReads:()=>{holdReads=true},evaluate:s=>vm.runInContext(s,ctx)};
}
(async()=>{
 // A successful approval remains successful after the user selects another year.
 let f=fixture(),{pending}=await f.start();
 await f.ctx.approveWorkpaperUI(1);await f.ctx.approveWorkpaperUI(1);
 assert.equal(f.requests.length,1,'duplicate clicks must not release the pending request lock');
 f.ctx.updateReviewRow(0,'amount','99');assert.equal(f.evaluate('reviewEditor.rows[0].amount'),'10','submitted rows are locked');
 f.$('validateYear').value='2027';f.ctx.changeReviewYear();f.approval.resolve();await pending;
 assert.equal(f.papers[0].status,'APPROVED');assert.ok(f.messages.some(x=>x.includes('2026사업연도 승인 완료')));assert.ok(!f.messages.some(x=>x.includes('승인 실패')));
 // A response for paper 1 must not erase dirty paper 2.
 f=fixture();({pending}=await f.start());f.ctx.showWorkpaperUI(2);f.ctx.updateReviewRow(0,'legal_basis','다른 검증표의 새 편집 내용');f.approval.resolve();await pending;
 assert.equal(f.evaluate('reviewEditor.id'),2);assert.equal(f.evaluate('reviewEditor.dirty'),true);assert.equal(f.evaluate('reviewEditor.rows[0].legal_basis'),'다른 검증표의 새 편집 내용');
 // Even a delayed follow-up refresh cannot overwrite a newly opened editor.
 f=fixture();({pending}=await f.start());f.holdReads();f.approval.resolve();await new Promise(resolve=>setImmediate(resolve));f.ctx.showWorkpaperUI(2);f.ctx.updateReviewRow(0,'label','새 검토 원인');f.reads.resolve();await pending;
 assert.equal(f.evaluate('reviewEditor.id'),2);assert.equal(f.evaluate('reviewEditor.rows[0].label'),'새 검토 원인');assert.equal(f.evaluate('reviewEditor.dirty'),true);
 // A delayed cancellation must also leave another workpaper's edits intact.
 f=fixture();await f.ctx.refreshReviewUI();f.ctx.showWorkpaperUI(1);const cancelling=f.ctx.cancelWorkpaperUI(1);f.ctx.showWorkpaperUI(2);f.ctx.updateReviewRow(0,'label','취소 대기 중 새 편집');f.cancellation.resolve();await cancelling;
 assert.equal(f.evaluate('reviewEditor.id'),2);assert.equal(f.evaluate('reviewEditor.dirty'),true);assert.equal(f.evaluate('reviewEditor.rows[0].label'),'취소 대기 중 새 편집');
 // Failed requests retain their submitted draft and allow retry.
 f=fixture();({pending}=await f.start());f.approval.reject(Error('통신 실패'));await pending;assert.equal(f.evaluate('reviewEditor.dirty'),true);assert.equal(f.evaluate('reviewEditor.submitting'),false);
 // The issue form captures its run; an old event cannot attach notes to a new run.
 f=fixture();await f.ctx.refreshReviewUI();f.ctx.reviewIssue(encodeURIComponent('VAL_MISSING|1'));let save=f.$('issueReviewSave').onclick;f.$('issueReviewStatus').value='ACCEPTED';f.$('issueReviewReason').value='기존 실행의 근거';f.ctx.lastValidationRun=2;await save();assert.equal(f.requests.length,0);
 f.ctx.reviewIssue(encodeURIComponent('VAL_MISSING|1'));save=f.$('issueReviewSave').onclick;await save();assert.equal(f.requests.at(-1).body.run_id,2);
 f.ctx.reviewIssue(encodeURIComponent('VAL_MISSING|1'));save=f.$('issueReviewSave').onclick;f.$('validateYear').value='2027';f.ctx.changeReviewYear();assert.equal(f.$('issueReviewEditor').innerHTML,'');await save();assert.equal(f.requests.length,1);
 // A new validation result invalidates a previously retained submit closure.
 f.ctx.lastValidationRun=3;f.ctx.reviewIssue(encodeURIComponent('VAL_MISSING|1'));save=f.$('issueReviewSave').onclick;f.ctx.renderValidationResult({issues:[]});await save();assert.equal(f.requests.length,1);
 console.log('PASS: deferred approval/year/paper/refresh races, duplicate submission lock, failure retention and frozen issue-run identity');
})().catch(error=>{console.error(error);process.exitCode=1});
