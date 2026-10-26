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
const engine=require('../../frontend/js/calculation.js');
function calculation(asOf='9999-12-31'){return engine.calculate(db,asOf,{accountingMethod:selectedAccountingMethod,taxMethod:selectedTaxMethod});}
let result=calculation();
assert.equal(result.ledger.find(x=>x.sourceId===3).taxPL,50);
assert.equal(result.ledger.find(x=>x.sourceId===4).taxPL,50);
assert.equal(result.positions.reduce((n,p)=>n+p.tax,0),200);
policy='MOVING';result=calculation();
assert.equal(result.ledger.find(x=>x.sourceId===3).taxPL,50);
assert.equal(result.ledger.find(x=>x.sourceId===4).taxPL,50);
console.log('PASS: pooled cost across FVOCI/FVTPL, total and moving');
