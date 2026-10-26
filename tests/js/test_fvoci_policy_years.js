const assert=require('node:assert/strict');
const {cases}=require('../fixtures/fvoci_policy_years.json');
const engine=require('../../frontend/js/calculation.js');
const fixtureState=example=>({
 assets:[{id:1,stock_code:'A',classification:'FVOCI'}],
 trades:[{id:1,assetId:1,date:'2025-01-01',type:'BUY',qty:10,amount:100},
 {id:2,assetId:1,date:'2026-07-01',type:'SELL',qty:4,amount:72},
 {id:3,assetId:1,date:'2027-07-01',type:'SELL',qty:6,amount:90}],
 valuations:[{id:1,assetId:1,date:'2025-12-31',price:14},{id:2,assetId:1,date:'2026-12-31',price:14}],
 taxPolicy:{method:'MOVING'},fvociPolicy:{disposal:example.baseline},
 fvociPolicyYears:example.policies.map(([effectiveYear,disposal])=>({effectiveYear,disposal,reviewer:'확인자',reason:'연도별 적용 검토'}))
});
for(const example of cases){
 const state=fixtureState(example),original=JSON.stringify(state);
 for(const [year,qty,book,tax,reserve,oci,activeOCI,retainedDisposalOCI] of example.checks){
  const result=engine.calculate(state,year+'-12-31');
  for(const [field,value] of Object.entries({qty,book,tax,reserve,oci,activeOCI,retainedDisposalOCI}))
   assert.equal(result.positions[0][field],value,`${example.id}/${year}/${field}`);
 }
 const final=engine.calculate(state,'2027-12-31');
 for(const [id,transfer,policy] of example.transfers){
  const row=final.ledger.find(r=>r.kind==='trade'&&r.sourceId===id);
  assert.equal(row.ociEquityTransfer,transfer);assert.equal(row.fvociDisposalPolicy,policy);
 }
 assert.equal(JSON.stringify(state),original);
 assert.equal(engine.fvociPolicyForYear(state,2024),example.baseline);
 assert.equal(engine.fvociPolicyForYear(state,2030),example.policies.at(-1)[1]);
}
const state=fixtureState(cases[0]);
for(const rows of [null,{},[{effectiveYear:2026,disposal:'RETAIN'}],
 [...state.fvociPolicyYears,state.fvociPolicyYears[0]],
 [{...state.fvociPolicyYears[0],effectiveYear:'2026'}],
 [{...state.fvociPolicyYears[0],disposal:'OTHER'}]])
 assert.throws(()=>engine.calculate({...state,fvociPolicyYears:rows},'2026-12-31'),/FVOCI/);

// Exercise the real policy editor without browser storage or user data.
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../../frontend/index.html'),'utf8');
const editor=html.slice(html.indexOf('function renderFvociPolicy(){'),html.indexOf('function saveFiscalMonth(){'));
const nodes=new Map();const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'',innerHTML:'',textContent:''});return nodes.get(id)};
const messages=[];
const context=vm.createContext({$,db:{fvociPolicy:{disposal:'RETAIN',history:[{disposal:'RETAIN',reason:'기존 기록'}]}},
 dbReady:true,dbSaveFailed:false,dbSaveQueue:Promise.resolve(),InvestmentEngine:engine,confirm:()=>true,
 notify:s=>messages.push(s),renderAll(){},fiscalYear:()=>2026,today:()=> '2026-10-26',esc:String,table:()=>''});
new vm.Script(editor).runInContext(context);
(async()=>{
 const baseline=JSON.stringify(context.db.fvociPolicy);
 $('fvociEffectiveYear').value='2027';$('fvociDisposal').value='TRANSFER';$('fvociReviewer').value='확인자';$('fvociReason').value='차기 사업연도 처분에 적용';
 context.queueDbSave=()=>{context.dbSaveQueue=Promise.resolve()};
 await context.saveFvociPolicy();
 assert.equal(JSON.stringify(context.db.fvociPolicy),baseline,'legacy baseline/history stays intact');
 assert.equal(context.db.fvociPolicyYears[0].effectiveYear,2027);
 $('fvociDisposal').value='RETAIN';await context.saveFvociPolicy();
 assert.equal(context.db.fvociPolicyYears.length,1);assert.equal(context.db.fvociPolicyYears[0].history.length,2);
 const previous=context.db.fvociPolicyYears;
 messages.length=0;
 context.queueDbSave=()=>{context.dbSaveQueue=Promise.resolve().then(()=>{context.dbSaveFailed=true})};
 $('fvociDisposal').value='TRANSFER';await context.saveFvociPolicy();
 assert.equal(context.db.fvociPolicyYears,previous);
 assert.ok(messages.some(s=>s.startsWith('정책 저장 실패:')));
 assert.ok(!messages.some(s=>s==='FVOCI 사업연도별 정책 저장 완료'));
 console.log('PASS: annual FVOCI answers, policy inheritance, legacy/history preservation and editor save failure');
})().catch(e=>{console.error(e);process.exitCode=1});
