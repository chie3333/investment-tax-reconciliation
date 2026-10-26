const assert=require('node:assert/strict');const fs=require('fs');const vm=require('vm');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../../frontend/index.html'),'utf8');const src=html.slice(html.indexOf('function fiscalYear('),html.indexOf('function computedAsset('));
function run(data,asOf){const db={fiscalEndMonth:3,...data};const ctx={db,roundW:n=>Math.round((n+Number.EPSILON)*100)/100,selectedAccountingMethod:()=> 'MOVING',selectedTaxMethod:(code,year)=>year==='2027'?'MOVING':'TOTAL',Date,Math,Number,Map,Set};vm.createContext(ctx);vm.runInContext(src,ctx);return {res:vm.runInContext(`calculation('${asOf}')`,ctx),fy:vm.runInContext("fiscalYear('2026-04-01')",ctx)};}
const a={id:1,stock_code:'A',classification:'FVOCI'},b={id:2,stock_code:'A',classification:'FVTPL'};
const data={assets:[a,b],trades:[{id:1,assetId:1,date:'2026-05-01',type:'BUY',qty:10,amount:100},{id:2,assetId:2,date:'2026-06-01',type:'BUY',qty:10,amount:300},{id:3,assetId:1,date:'2027-02-01',type:'SELL',qty:5,amount:100}],valuations:[{id:1,assetId:1,date:'2027-01-01',price:15}]};
let {res,fy}=run(data,'2027-03-31');assert.equal(fy,'2027');assert.equal(res.reconciliation[0].qty,15);assert.equal(res.reconciliation[0].tax,300);assert.equal(res.positions.reduce((n,p)=>n+p.tax,0),300);const sell=res.ledger.find(x=>x.type==='SELL');assert.equal(sell.taxPL,0);assert.equal(sell.adj,0);assert.equal(sell.ociRecycling,false);
// FVOCI OCI valuation gain and disposal bridge; no profit recycling
const v=res.ledger.find(x=>x.type==='VALUATION');assert.equal(v.ociChange,50);assert.equal(v.ociTaxBridge,0);
const full={assets:[a],trades:[{id:1,assetId:1,date:'2026-04-01',type:'BUY',qty:10,amount:100},{id:2,assetId:1,date:'2027-02-01',type:'SELL',qty:10,amount:150}],valuations:[{id:1,assetId:1,date:'2026-12-31',price:13}]};const f=run(full,'2027-03-31').res;assert.equal(f.positions[0].qty,0);assert.equal(f.positions[0].tax,0);assert.equal(f.positions[0].reserve,0);assert.equal(f.ledger.find(x=>x.type==='SELL').ociTaxBridge,50);assert.equal(f.ledger.find(x=>x.type==='SELL').adj,50);
console.log('PASS: 3월 결산 사업연도, 통합 원가 배분, FVOCI 평가/처분 OCI 브리지, 전량 처분 잔액');
