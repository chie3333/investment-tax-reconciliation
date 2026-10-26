const assert=require('node:assert/strict');
const engine=require('../../frontend/js/calculation.js');
function calc(data){return engine.calculate({fiscalEndMonth:12,...data},'2026-12-31',{accountingMethod:()=> 'MOVING',taxMethod:()=> 'MOVING'});}
const a={id:1,stock_code:'AAA',classification:'FVOCI'},b={id:2,stock_code:'BBB',classification:'FVTPL'};
const trades=[{id:1,assetId:1,date:'2026-01-01',type:'BUY',qty:10,amount:100},{id:2,assetId:1,date:'2026-10-01',type:'SELL',qty:5,amount:90}];
const vals=[{id:1,assetId:1,date:'2026-06-30',price:14}];
let res=calc({assets:[a],trades,valuations:vals});
const sale=res.ledger.find(r=>r.type==='SELL');
assert.equal(sale.bookPL,0);assert.equal(sale.ociChange,20);assert.equal(sale.ociEquityTransfer,40);assert.equal(sale.directEquityDisposal,0);assert.equal(sale.taxPL,40);assert.equal(sale.adj,40);assert.equal(sale.taxDisposition,'REVIEW_REQUIRED');assert.equal(sale.ociRecycling,false);
assert.equal(res.positions[0].oci,20);assert.equal(res.positions[0].reserve,-20);
const valuation=res.ledger.find(r=>r.type==='VALUATION');assert.equal(valuation.ociChange,40);assert.equal(valuation.adj,0);
res=calc({assets:[a],trades:[...trades,{id:3,assetId:1,date:'2026-11-01',type:'SELL',qty:5,amount:100}],valuations:vals});
assert.equal(res.positions[0].qty,0);assert.equal(res.positions[0].reserve,0);assert.equal(res.positions[0].oci,0);
res=calc({assets:[b],trades:[{id:1,assetId:2,date:'2026-01-01',type:'BUY',qty:10,amount:100},{id:2,assetId:2,date:'2026-10-01',type:'SELL',qty:10,amount:150}],valuations:[]});
assert.equal(res.ledger.find(r=>r.type==='SELL').bookPL,50);assert.equal(res.ledger.find(r=>r.type==='SELL').adj,0);
console.log('PASS FVOCI OCI/equity/disposal and FVTPL P&L separation');
