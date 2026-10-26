const assert = require('node:assert/strict');
const {calculate, fiscalEndDate} = require('../../frontend/js/calculation.js');
const {cases} = require('../fixtures/fvoci_cases.json');

let checks = 0;
for (const example of cases) {
 for (const disposal of ['TRANSFER', 'RETAIN']) {
  const state = {...example.state, fvociPolicy: {disposal}};
  const original = JSON.stringify(state);
  for (const checkpoint of example.checks) {
   const result = calculate(state, checkpoint.asOf);
   for (const expected of checkpoint.positions) {
    const p = result.positions.find(p => p.asset.id === expected.id);
    for (const field of ['qty','book','tax','reserve','activeOCI']) {
     assert.equal(p[field], expected[field], `${example.id}/${disposal}/${checkpoint.asOf}/${field}`);
    }
    assert.equal(p.oci, disposal === 'TRANSFER' ? expected.oci : expected.retainedOCI);
    assert.equal(p.retainedDisposalOCI, disposal === 'TRANSFER' ? 0 : expected.retainedOCI - expected.activeOCI);
    // Every asset's changes, including cross-classification allocation, reconcile.
    const movements = result.ledger.flatMap(r => r.allocationEffects || [])
     .filter(e => e.assetId === expected.id).reduce((n,e) => n+e.reserveChange, 0);
    assert.ok(Math.abs(movements-p.reserve)<0.011);
   }
   const event = checkpoint.event;
   const row = result.ledger.find(r => r.kind === event.kind && r.sourceId === event.id);
   for (const [key,value] of Object.entries(event)) {
    if (['kind','id','transfer'].includes(key)) continue;
    assert.equal(row[key], value, `${example.id}/${disposal}/${event.id}/${key}`);
   }
   if (event.transfer !== undefined) assert.equal(row.ociEquityTransfer, disposal === 'TRANSFER' ? event.transfer : 0);
   assert.equal(row.ociRecycling, false);
   assert.equal(row.taxDisposition, 'REVIEW_REQUIRED');
   assert.equal(row.directEquityDisposal, 0);
   checks++;
  }
  assert.equal(JSON.stringify(state), original, 'calculation must not mutate input');
 }
}

// Rounding residual goes to a held asset, never to a zero-quantity position.
const rounding = calculate({assets:[1,2,3,4].map(id=>({id,stock_code:'A',classification:'FVOCI'})),
 trades:[100,100,101].map((amount,i)=>({id:i+1,assetId:i+1,date:'2026-01-01',type:'BUY',qty:1,amount})),valuations:[]});
assert.deepEqual(rounding.positions.map(p=>p.tax), [100.33,100.33,100.34,0]);
assert.equal(rounding.reconciliation[0].tax,301);
assert.equal(fiscalEndDate(2028,2),'2028-02-29');
assert.throws(()=>calculate({...cases[0].state,fvociPolicy:{disposal:'TYPO'}}),/정책/);

// A tax-method change begins at the fiscal-year boundary, not January 1.
const fiscal = {fiscalEndMonth:3,assets:[{id:1,stock_code:'A',classification:'FVOCI'}],valuations:[],
 taxPolicyYears:[{effectiveYear:'2026',method:'TOTAL'},{effectiveYear:'2027',method:'MOVING'}],
 trades:[{id:1,assetId:1,date:'2026-03-01',type:'BUY',qty:10,amount:100},
 {id:2,assetId:1,date:'2026-04-01',type:'SELL',qty:5,amount:90},
 {id:3,assetId:1,date:'2026-09-01',type:'BUY',qty:10,amount:300}]};
assert.equal(calculate(fiscal,'2027-03-31').ledger.find(r=>r.type==='SELL').taxPL,40);

// A new holding must not absorb OCI retained from an earlier fully disposed lot.
const prior = cases.find(c=>c.id==='repeated_partial').state;
const repurchase = {...prior, fvociPolicy:{disposal:'RETAIN'},
 trades:[...prior.trades,{id:5,assetId:1,date:'2026-12-01',type:'BUY',qty:10,amount:100},
 {id:6,assetId:1,date:'2026-12-20',type:'SELL',qty:5,amount:70}],
 valuations:[...prior.valuations,{id:2,assetId:1,date:'2026-12-10',price:12}]};
const repurchased = calculate(repurchase,'2026-12-31').positions[0];
assert.equal(repurchased.oci,91);
assert.equal(repurchased.activeOCI,10);
assert.equal(repurchased.retainedDisposalOCI,81);
console.log(`PASS: ${cases.length} FVOCI answer cases, ${checks} policy/checkpoint comparisons, allocation and fiscal-year checks`);
