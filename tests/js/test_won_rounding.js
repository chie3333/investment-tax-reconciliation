const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const engine=require('../../frontend/js/calculation.js');
const answers=require('../fixtures/won_rounding.json');
for(const [value,expected] of answers.rounding)assert.equal(engine.roundWon(value),expected,value);
assert.equal(engine.decimalProduct(3,'0.16666666666666666'),'0.49999999999999998');
assert.equal(engine.roundWon(engine.decimalProduct(3,'0.16666666666666666')),0);
for(const method of ['TOTAL','MOVING'])for(const c of answers.disposals){
 const state={roundingPolicy:{profile:'KRW_WON_V1',effectiveYear:2026},assets:[{id:1,stock_code:'A',classification:'FVTPL'}],taxPolicy:{method},valuations:[],
  trades:[{id:1,assetId:1,date:'2026-01-01',type:'BUY',qty:c.qty,amount:c.cost},...c.costs.map((_,i)=>({id:i+2,assetId:1,date:`2026-02-${String(i+1).padStart(2,'0')}`,type:'SELL',qty:1,amount:c.proceeds}))]};
 const result=engine.calculate(state,'2026-12-31');assert.ok(result.ledger.every(r=>!r.error));
 const sales=result.ledger.filter(r=>r.type==='SELL');
 assert.deepEqual(sales.map(r=>c.proceeds-r.taxPL),c.costs);
 assert.deepEqual(sales.map(r=>r.book),c.remaining);
 assert.equal(result.positions[0].book,0);assert.equal(result.positions[0].tax,0);
 state.trades=state.trades.slice(0,1);assert.equal(engine.calculate(state,'2026-12-31').positions[0].book,c.cost);
 delete state.roundingPolicy;assert.equal(engine.calculate(state,'2026-12-31').positions[0].tax,c.cost,'legacy large integer has no epsilon drift');
}
const html=fs.readFileSync(path.join(__dirname,'../../frontend/index.html'),'utf8');
assert.ok(!html.includes('Number.EPSILON'),'the form preview must not add binary epsilon either');
assert.match(html,/maximumFractionDigits:0|InvestmentEngine\.roundWon\(n\)/);
assert.match(html,/Object\.hasOwn\(result\.state,'roundingPolicy'\)/,'loading old states must not inject new defaults');
// Execute the editor's real save-failure branch: no false success or changed local policy.
const editor=html.slice(html.indexOf('function renderRoundingPolicy(){'),html.indexOf('function saveFiscalMonth(){'));
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{value:2026,textContent:''});return nodes.get(id)};
const messages=[],ctx=vm.createContext({$,db:{},dbReady:true,dbSaveFailed:false,dbSaveQueue:Promise.resolve(),InvestmentEngine:{calculate:()=>({ledger:[]}),fiscalEndDate:()=> '2026-12-31'},
 confirm:()=>true,notify:s=>messages.push(s),renderAll(){},fiscalYear:()=>2026,today:()=> '2026-10-26'});
new vm.Script(editor).runInContext(ctx);
(async()=>{
 // Real trade/valuation handlers must multiply decimal input before Number conversion.
 const formNodes=new Map(),field=id=>{if(!formNodes.has(id))formNodes.set(id,{value:''});return formNodes.get(id)};
 for(const [id,value] of Object.entries({tDate:'2026-01-01',tStock:'A',tClass:'FVTPL',tQty:'3',tPrice:'0.16666666666666666',tNote:''}))field(id).value=value;
 const formState={roundingPolicy:{profile:'KRW_WON_V1',effectiveYear:2026},master:[{code:'A',name:'예제',market:'USER'}],assets:[],trades:[],valuations:[],ids:{asset:0,trade:0,valuation:0}};
 const form=vm.createContext({$:field,db:formState,activeTrade:'BUY',InvestmentEngine:engine,fiscalYear:()=>2026,getAsset:()=>formState.assets[0],holdings:()=>3,persist(){},notify(){},calculationBeforeValuation:()=>0});
 new vm.Script(html.slice(html.indexOf('function inputCash('),html.indexOf('updateValuationPreview=function()'))).runInContext(form);
 form.saveTrade({preventDefault(){}});
 assert.equal(formState.trades[0].amount,0);assert.equal(formState.trades[0].rawAmount,'0.49999999999999998');
 assert.equal(formState.trades[0].price,'0.16666666666666666');
 for(const [id,value] of Object.entries({vAsset:'1',vDate:'2026-12-31',vPrice:'0.16666666666666666',vNote:''}))field(id).value=value;
 new vm.Script(html.slice(html.indexOf('saveValuation=function(e)'),html.indexOf('renderReports=function()'))).runInContext(form);
 form.saveValuation({preventDefault(){}});assert.equal(formState.valuations[0].fairValue,0);
 assert.equal(formState.valuations[0].rawFairValue,'0.49999999999999998');
 ctx.queueDbSave=()=>{ctx.dbSaveQueue=Promise.resolve().then(()=>{ctx.dbSaveFailed=true})};
 await ctx.saveRoundingPolicy();assert.equal(ctx.db.roundingPolicy,undefined);
 assert.ok(messages.some(m=>m.startsWith('반올림 정책 저장 실패:')));assert.ok(!messages.includes('원 단위 반올림 기준 저장 완료'));
 // Run the real first inline script without a calculation module present yet.
 // DOMContentLoaded must defer monetary rendering until that module is loaded.
 const firstScript=html.match(/<script>([\s\S]*?)<\/script>/)[1];
 for(const configured of [false,true]){
  const elements=new Map(),callbacks=[];
  const node=()=>({value:'',style:{},innerHTML:'',textContent:'',classList:{toggle(){}},addEventListener(){},remove(){}});
  const doc={readyState:'loading',getElementById(id){if(!elements.has(id))elements.set(id,node());return elements.get(id)},querySelectorAll:()=>[],createElement:node,body:{appendChild(){}},addEventListener(event,fn){if(event==='DOMContentLoaded')callbacks.push(fn)}};
  const state={master:[],assets:[],trades:[],valuations:[],adjustments:[],taxPolicyYears:[],...(configured?{roundingPolicy:{profile:'KRW_WON_V1',effectiveYear:2027}}:{})};
  const boot=vm.createContext({document:doc,window:{addEventListener(){}},localStorage:{getItem:()=>null},fetch:async()=>({ok:true,json:async()=>({state,revision:7,backend:'normalized'})}),fiscalYear:()=>2026});
  new vm.Script(firstScript).runInContext(boot);assert.equal(callbacks.length,1);
  boot.InvestmentEngine=engine;
  await callbacks[0]();
  assert.match(elements.get('dbStatus').textContent,/연결됨.*7/);
  const saved=vm.runInContext('db.roundingPolicy',boot);
  assert.equal(saved?.effectiveYear,configured?2027:undefined,'old workspaces keep legacy arithmetic');
 }
 console.log('PASS: exact won ties, 33/33/34 residual, tiny/large costs, legacy preservation and editor failure');
})().catch(e=>{console.error(e);process.exitCode=1});
