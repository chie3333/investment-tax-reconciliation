const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname,'../../frontend/index.html'),'utf8');
const script = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1])
 .find(s=>s.includes('function saveTaxRuleScopeUI('));
const elements = new Map();
const $ = id=>{if(!elements.has(id))elements.set(id,{value:'',checked:false,innerHTML:''});return elements.get(id)};
const messages=[];
const context = vm.createContext({InvestmentReview:require('../../frontend/js/review-model.js'),window:{addEventListener(){}},dbRevision:1,document:{readyState:'loading',addEventListener(){}},$,
 db:{assets:[{id:1,stock_code:'A',asset_name:'종목 A',classification:'FVOCI'}]},dbReady:true,dbSaveFailed:false,
 dbSaveQueue:Promise.resolve(),confirm:()=>true,notify:s=>messages.push(s),
 esc:s=>String(s??'').replaceAll('<','&lt;'),won:n=>String(n),
 assetLabel:a=>a.asset_name,getAsset:()=>({asset_name:'종목 A'}),fiscalYear:()=>2026,
 table:(headers,rows)=>headers.join(' ')+rows.flat().join(' ')});
new vm.Script(script).runInContext(context);
new vm.Script(fs.readFileSync(path.join(__dirname,'../../frontend/js/review.js'),'utf8')).runInContext(context);
assert.equal(context.InvestmentReview.rowsFor({status:'DRAFT',candidate:{candidate_adjustment:40,reserve_change:20}}).length,0,'no generic plug row');
assert.equal(context.InvestmentReview.rowsFor({status:'DRAFT',candidate:{adjustment_plan:{status:'MANUAL_REQUIRED',rows:[{}]}}}).length,0);
const row={asset_id:'1',direction:'익금산입',disposition:'기타',amount:'20.00',reserve_movement:'0.00',legal_basis:'법인세법 검토 근거',label:'당기 OCI 자본 연결'};
const plan={version:'kr-listed-2026-v1',status:'REVIEW_REQUIRED',rows:[row],reasons:[]};
const copied=context.InvestmentReview.rowsFor({status:'DRAFT',candidate:{adjustment_plan:plan}});copied[0].amount='999';assert.equal(row.amount,'20.00');
context.fixture=[{id:1,source_revision:1,status:'DRAFT',checks:[],candidate:{asset_id:1,asset_name:'종목 A',stock_code:'A',
 classification:'FVOCI',event:'SELL',date:'2026-07-01',book_pl:'0',tax_pl:'40',oci_change:'20',
 oci_equity_transfer:'40',candidate_adjustment:'40',reserve_change:'20',adjustment_plan:plan,allocation_effects:[]}}];
vm.runInContext('currentWorkpapers=fixture;reviewYear=2026;showWorkpaperUI(1)',context);
assert.equal(vm.runInContext('reviewEditor.rows[0].legal_basis',context),row.legal_basis);
assert.ok(!$('workpaperDetail').innerHTML.includes('JSON 배열'));
assert.ok($('reviewRowsHost').innerHTML.includes('검토 근거'));
assert.ok($('workpaperDetail').innerHTML.includes('이익잉여금 대체'));
assert.ok($('reviewRowsHost').innerHTML.includes('당기 OCI 자본 연결'));
assert.ok($('workpaperDetail').innerHTML.includes('독립 과세소득 조정액은 0원'));

(async()=>{
 $('taxRuleConfirm').checked=true;$('taxRuleReviewer').value='확인자';$('taxRuleNote').value='예외 없음 확인';
 context.queueDbSave=()=>{context.dbSaveQueue=Promise.resolve()};
 await context.saveTaxRuleScopeUI();
 assert.equal(context.db.taxAdjustmentScope.confirmed,true);
 assert.equal(context.db.taxAdjustmentScope.year,2026);
 const original=context.db.taxAdjustmentScope;
 messages.length=0;
 context.queueDbSave=()=>{context.dbSaveQueue=Promise.resolve().then(()=>{context.dbSaveFailed=true})};
 await context.saveTaxRuleScopeUI();
 assert.equal(context.db.taxAdjustmentScope,original,'failed save restores prior scope');
 assert.ok(messages.some(s=>s.startsWith('저장 실패:')));
 assert.ok(!messages.some(s=>s.startsWith('적용범위를 저장했습니다')));
 console.log('PASS: server-only rule drafts, scope confirmation, prefill and save failure');
})().catch(error=>{console.error(error);process.exitCode=1});
