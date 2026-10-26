const assert = require('node:assert/strict');
const {cases} = require('../fixtures/fvoci_reserve_cases.json');
const {calculate} = require('../../frontend/js/calculation.js');
let checks = 0;
for (const example of cases) {
 for (const disposal of ['TRANSFER','RETAIN']) {
  const state = {
   assets:example.assets.map(([id,classification])=>({id,classification,stock_code:'A',asset_name:'검산 종목'})),
   trades:example.trades.map(([id,assetId,date,type,qty,amount])=>({id,assetId,date,type,qty,amount,price:amount/qty})),
   valuations:example.valuations.map(([id,assetId,date,price])=>({id,assetId,date,price:Number(price)})),
   taxPolicy:{method:example.method},fvociPolicy:{disposal}
  };
  for (const [id,removed,cost,movement,reserve] of example.sales) {
   const trade = state.trades.find(t=>t.id===id);
   const result = calculate(state,trade.date);
   const row = result.ledger.find(r=>r.kind==='trade'&&r.sourceId===id);
   const position = result.positions.find(p=>p.asset.id===trade.assetId);
   assert.equal(row.reserveChange,movement,`${example.id}/${disposal}/${id}/movement`);
   assert.equal(position.reserve,reserve,`${example.id}/${disposal}/${id}/reserve`);
   // Separately rounded components may differ by one cent; balances above are exact.
   assert.ok(Math.abs((trade.amount-row.taxPL)-cost)<0.011);
   assert.ok(Math.abs(row.ociChange-(trade.amount-removed))<0.011);
   assert.equal(row.bookPL,0);
   checks++;
  }
  const year = [...state.trades,...state.valuations].map(e=>e.date.slice(0,4)).sort().at(-1);
  const result = calculate(state,year+'-12-31');
  for (const [id,qty,book,tax,reserve] of example.ending) {
   const actual = result.positions.find(p=>p.asset.id===id);
   for (const [field,value] of Object.entries({qty,book,tax,reserve})) assert.equal(actual[field],value,`${example.id}/${field}`);
  }
 }
}
console.log(`PASS: ${cases.length} reserve cases, ${checks} sale/policy checkpoints`);
