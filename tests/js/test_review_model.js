const assert=require('node:assert/strict'),M=require('../../frontend/js/review-model.js');
const candidate={asset_id:1,candidate_adjustment:'40',reserve_change:'20',adjustment_plan:{rounding_profile:'KRW_WON_V1'},allocation_effects:[{assetId:1,reserveChange:'20'}]};
const rows=[{asset_id:'1',direction:'익금산입',disposition:'기타',amount:'20',reserve_movement:'0',legal_basis:'자본 연결'},
 {asset_id:'1',direction:'손금산입',disposition:'△유보',amount:'20',reserve_movement:'-20',legal_basis:'평가 부인'},
 {asset_id:'1',direction:'익금산입',disposition:'유보',amount:'40',reserve_movement:'40',legal_basis:'처분 환입'}];
let t=M.totals(rows,candidate);assert.equal(t.income,'40');assert.equal(t.reserve,'20');assert.ok(t.incomeMatch&&t.reserveMatch);assert.deepEqual(t.errors,[]);
assert.equal(M.movement({amount:'40',disposition:'△유보'}),'-40');assert.equal(M.movement({amount:'40',disposition:'기타'}),'0');
assert.ok(!M.totals(rows.slice(1),candidate).incomeMatch);
const wrong=structuredClone(rows);wrong[2].asset_id='2';assert.ok(!M.totals(wrong,candidate).reserveMatch,'matching gross sum cannot conceal wrong target');
for(const amount of ['0','-1','0.5','NaN','1e3','1000000000000001']){const bad=structuredClone(rows);bad[0].amount=amount;assert.ok(M.totals(bad,candidate).errors.length,amount);}
assert.throws(()=>M.units('<img>'));assert.equal(M.units('10000000000000.01'),1000000000000001n);
assert.equal(M.display('-10000000000000.01'),'−10,000,000,000,000.01원');
assert.equal(M.reconciliation('-40','-20','20'),'승인 대기 차이');
assert.equal(M.reconciliation('-40','-20','19'),'원장 차이 · 원인 확인');
assert.equal(M.reconciliation('-40','-20','20',false),'원장 차이 · 원인 확인');
assert.equal(M.reconciliation('-20','-20','0'),'일치');
const w={status:'APPROVED',candidate:{adjustment_plan:{status:'REVIEW_REQUIRED',rows}},approved:{adjustments:[{amount:'99',legal_basis:'승인 당시 근거'}]}};
assert.equal(M.rowsFor(w)[0].amount,'99');w.status='CANCELLED';assert.equal(M.rowsFor(w)[0].amount,'99');
w.status='DRAFT';assert.equal(M.rowsFor(w)[0].amount,'20');
M.rowsFor(w)[0].amount='100';assert.equal(rows[0].amount,'20');
assert.equal(M.itemLabel({candidate:{asset_name:'검토주식',classification:'FVOCI',event:'SELL',review_detail:{quantity_after:5}}}),'검토주식 · FVOCI · 부분 처분');
console.log('PASS: exact editable row totals, asset-specific checks, frozen approvals and pending-ledger classification');
