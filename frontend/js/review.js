/* Review workflow UI. The server remains the authority for approval and closing. */
let currentWorkpapers=[],reviewEditor=null,reviewClosing=null,reviewYear=null,reviewRequest=0,reviewEdits=0,issueReviewContext=null;
const pendingApprovals=new Set();
const RM=InvestmentReview;
function reviewYearValue(){const y=Number($('validateYear').value);if(!Number.isInteger(y)||y<1900||y>2200)throw Error('사업연도를 확인하세요.');return y}
function stalePaper(w){return w.source_revision!==dbRevision&&!(reviewClosing?.year===reviewYear&&reviewClosing.close)}
function paperStatus(w){return stalePaper(w)&&['DRAFT','APPROVED'].includes(w.status)?'원천 변경 · 재검토 필요':RM.statuses[w.status]||'상태 미확인'}
function statusBadge(w){return `<span class="review-status ${stalePaper(w)?'stale':esc(w.status)}">${esc(paperStatus(w))}</span>`}
function canLeaveReview(page){return page==='validation'||!reviewEditor?.dirty||confirm('승인 전 편집 내용은 저장되지 않았습니다. 화면을 이동할까요?')}
function discardReviewEdits(){return !reviewEditor?.dirty||confirm('승인 전 편집 내용을 버리고 새로 불러올까요?')}
function resetIssueReview(){issueReviewContext=null;$('issueReviewEditor').innerHTML='';}
function changeReviewYear(){if(!discardReviewEdits()){$('validateYear').value=reviewYear;return}reviewEditor=null;reviewClosing=null;currentWorkpapers=[];$('workpaperDetail').innerHTML='';$('validationResults').innerHTML='';$('validationSummary').textContent='선택한 사업연도·현재 저장 버전으로 검증하세요.';lastValidationRun=null;resetIssueReview();refreshReviewUI()}
async function refreshReviewUI(options={}){
 const guarded=Object.hasOwn(options,'expectedEditor');
 const stillCurrent=()=>!guarded||reviewEditor===options.expectedEditor;
 if(!stillCurrent())return false;
 if(!dbReady)return;
 if(!discardReviewEdits())return;
 try{
  await dbSaveQueue;if(dbSaveFailed)throw Error('저장 실패를 먼저 해결하세요.');
  if(!stillCurrent())return false;
  const year=reviewYearValue(),revision=dbRevision,request=++reviewRequest,editsAtStart=reviewEdits;
  const [papers,closing]=await Promise.all([dbRequest('/api/workpapers?year='+year),dbRequest('/api/closing/summary?year='+year)]);
  if(request!==reviewRequest||reviewYearValue()!==year||dbRevision!==revision||!stillCurrent())return false;
  if(reviewEditor?.dirty&&reviewEdits!==editsAtStart){notify('편집 내용이 있어 새로고침을 적용하지 않았습니다.');return;}
  reviewYear=year;currentWorkpapers=papers.items;reviewClosing=closing;reviewEditor=null;$('workpaperDetail').innerHTML='';
  renderWorkpaperList();renderReviewOverview();renderClosingReview();return true;
 }catch(e){$('reviewOverview').textContent='검토 현황 조회 실패: '+e.message}
}
async function loadWorkpapersUI(){return refreshReviewUI()}
function renderReviewOverview(){
 const active=currentWorkpapers.filter(w=>['DRAFT','APPROVED'].includes(w.status));
 $('reviewOverview').innerHTML=[['승인 대기',active.filter(w=>w.status==='DRAFT').length],['승인 완료',active.filter(w=>w.status==='APPROVED').length],['재검토 필요',active.filter(stalePaper).length],['결산 상태',reviewClosing?.close?'확정':'미확정']].map(([label,value])=>`<div class="review-metric">${esc(label)}<strong>${esc(value)}</strong></div>`).join('');
}
function renderWorkpaperList(){
 const filter=$('reviewFilter')?.value||'ACTIVE';
 const rows=currentWorkpapers.filter(w=>filter==='ALL'||filter==='ACTIVE'&&['DRAFT','APPROVED'].includes(w.status)||w.status===filter);
 $('workpaperList').innerHTML=rows.length?table(['종목 · 분류 · 거래','일자','검토 상태','과세소득 조정','원천 자산 유보 증감','검토'],rows.map(w=>{
  const a=w.candidate,scale=RM.scaleOf(a);
  return [esc(RM.itemLabel(w)),esc(a.date),statusBadge(w),esc(RM.display(a.candidate_adjustment,scale)),esc(RM.display(a.reserve_change,scale)),`<button class="btn" onclick="showWorkpaperUI(${Number(w.id)})">검토 열기</button>`];
 })):'<div class="review-empty">표시할 검증표가 없습니다. 거래·평가를 확인한 뒤 현재 자료로 검증표를 생성하세요.</div>';
}
function calculationExplanation(a){
 const d=a.review_detail,scale=RM.scaleOf(a),cash=v=>esc(RM.display(v,scale));
 if(!d)return '<p class="notice">이전 회차에는 원천 계산 과정이 기록되어 있지 않습니다. 아래 보존 금액을 확인하세요. 현재 원천으로 과거 승인 내역을 다시 만들지 않습니다.</p>';
 const facts=[[a.event==='SELL'?'처분 수량':'대상 수량',d.quantity+'주'],['거래 전 보유',d.quantity_before+'주'],['거래 후 보유',d.quantity_after+'주']];
 let html='<div class="review-facts">'+facts.map(([label,value])=>`<div class="review-fact">${esc(label)}<b>${esc(value)}</b></div>`).join('')+'</div><div class="review-equations">';
 if(a.event==='SELL'){
  html+=`<div class="review-equation">매각대금 <b>${cash(d.gross_amount)}</b> − 세무 처분원가 <b>${cash(d.tax_cost)}</b> = 세법상 처분손익 <b>${cash(a.tax_pl)}</b></div>`;
  html+=`<div class="review-equation">매각대금 <b>${cash(d.gross_amount)}</b> − 회계 제거금액 <b>${cash(d.book_removed)}</b> = ${a.classification==='FVOCI'?'처분 시 OCI 변동':'회계 처분손익'} <b>${cash(a.classification==='FVOCI'?a.oci_change:a.book_pl)}</b></div>`;
  if(a.classification==='FVOCI')html+=a.fvoci_disposal_policy==='TRANSFER'?`<div class="review-equation">처분분의 기존 OCI ${cash(d.disposed_prior_oci)} + 처분 시 OCI 변동 ${cash(a.oci_change)} = 이익잉여금 대체 ${cash(a.oci_equity_transfer)}<br>회계상 당기손익으로 재분류하지 않습니다.</div>`:'<div class="review-equation">처분분의 누적 OCI를 OCI에 유지합니다. 회계상 당기손익으로 재분류하지 않습니다.</div>';
 }else if(a.event==='VALUATION'){
  html+=`<div class="review-equation">보유 ${esc(d.quantity_before)}주 × 입력 단가 ${esc(d.input_price)}원 → 반올림 후 평가금액 <b>${cash(d.book_after)}</b><br>평가 전 장부 ${cash(d.book_before)} → 평가 후 장부 ${cash(d.book_after)} · ${a.classification==='FVOCI'?'OCI 변동':'회계 평가손익'} ${cash(a.classification==='FVOCI'?a.oci_change:a.book_pl)}</div>`;
 }else html+=`<div class="review-equation">${esc(RM.events[a.event]||'거래')} 금액 ${cash(d.gross_amount)} · 회계장부 ${cash(d.book_before)} → ${cash(d.book_after)}</div>`;
 html+=`<div class="review-equation">유보 잔액 ${cash(d.reserve_before)} → ${cash(d.reserve_after)} · 원천 자산 증감 <b>${cash(a.reserve_change)}</b><br>거래 후 세무원가 ${cash(a.tax_basis)} − 회계장부 ${cash(a.book_basis)} = 유보 잔액 ${cash(d.reserve_after)}</div></div>`;
 return html;
}
function showWorkpaperUI(id){
 const w=currentWorkpapers.find(x=>x.id===id);if(!w)return;
 if(reviewEditor?.id!==id&&!discardReviewEdits())return;
 const a=w.candidate,editable=w.status==='DRAFT'&&!stalePaper(w)&&!reviewClosing?.close;
 if(!reviewEditor||reviewEditor.id!==id)reviewEditor={id,rows:RM.rowsFor(w),dirty:false,revision:dbRevision,year:reviewYear};
 const rows=RM.rowsFor(w),sources=(a.adjustment_plan?.sources||[]).filter(s=>/^https?:\/\//.test(s.url||''));
 $('workpaperDetail').innerHTML=`<article class="review-detail"><fieldset class="review-controls" ${reviewEditor.submitting?'disabled':''}><h3>${esc(RM.itemLabel(w))} ${statusBadge(w)}</h3><p>${esc(a.date)} · ${esc(a.stock_code)} · ${a.classification==='FVOCI'?(a.fvoci_disposal_policy==='TRANSFER'?'누적 OCI 이익잉여금 대체':'누적 OCI 유지'):''}</p>
 <h4>원천과 금액 계산 과정</h4>${calculationExplanation(a)}
 <div class="review-totals"><div>과세소득 조정 <b>${esc(RM.display(a.candidate_adjustment,RM.scaleOf(a)))}</b></div><div>원천 자산 유보 증감 <b>${esc(RM.display(a.reserve_change,RM.scaleOf(a)))}</b></div></div>
 ${(a.allocation_effects||[]).length>1?table(['배분 대상','세무원가','회계장부','유보 증감'],a.allocation_effects.map(e=>[esc(assetLabel(getAsset(e.assetId)||{id:e.assetId,asset_name:'보존 자산'})),esc(RM.display(e.tax,RM.scaleOf(a))),esc(RM.display(e.book,RM.scaleOf(a))),esc(RM.display(e.reserveChange,RM.scaleOf(a)))])):''}
 <h4>${editable?'조정 행 검토·편집':w.approved?'실제 승인된 조정 행':'보존된 조정 후보'}</h4>
 ${w.status==='CANCELLED'?'<p class="notice">승인 취소된 회차입니다. 아래는 취소 전 승인 원문이며 현재 승인으로 보지 않습니다.</p>':''}
 ${stalePaper(w)&&!reviewClosing?.close?'<p class="alert">원천이 변경되었습니다. 이 회차로 승인할 수 없습니다. 기존 승인은 취소 후 검증표를 재생성하여 검토하세요.</p>':''}
 ${a.adjustment_plan?.status!=='REVIEW_REQUIRED'?'<p class="alert">별도 법령 검토가 필요합니다. 자동 조정 행을 만들지 않았습니다. 근거를 확인한 수동 조정 행을 입력할 수 있으며 승인 시 서버가 적용범위·금액을 다시 검사합니다.</p>':''}
 <div id="reviewRowsHost"></div><div id="reviewTotals" aria-live="polite"></div>
 <details><summary>자동 검사·법령 근거·계산 기록 상세</summary>${(w.checks||[]).map(c=>`<p class="${c.manual?'hint':c.passed?'notice':'alert'}">${c.manual?'담당자 확인':c.passed?'검사 통과':'오류'} · ${esc(c.name)} ${esc(c.note||'')}</p>`).join('')}
 <p>${esc((a.adjustment_plan?.reasons||[]).join(' / '))}</p><p>법령 확인일 ${esc(a.adjustment_plan?.checked_on||'미기록')} · ${esc(a.adjustment_plan?.interpretation||'')}</p>${sources.map(s=>`<p><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a> · ${esc(s.edition)}</p>`).join('')}
 <p>계산 엔진 ${esc(a.calculation?.engine_version||'미기록')} · 원천 버전 ${esc(w.source_revision)} · 계산 식별값 ${esc(a.calculation?.digest||'미기록')} · 내부 참조 ${esc(w.source_key)}</p></details>
 ${editable?`<div class="review-approval"><h4>검토 완료 후 승인</h4><p class="hint">표의 변경사항은 승인할 때 저장됩니다. 합계 일치만으로 법적 적정성이 확인되지는 않습니다. 이익잉여금 대체는 자본 내 이동이며 독립 과세소득 조정액은 0원입니다.</p><div class="formgrid"><label class="field">검토자<input id="wpReviewer" value="${esc(reviewEditor.reviewer||'')}" oninput="updateReviewApproval('reviewer',this.value)"></label><label class="field full">검토 의견 (10자 이상)<textarea id="wpNote" oninput="updateReviewApproval('note',this.value)">${esc(reviewEditor.note||'')}</textarea></label><label class="field full"><span><input id="wpAcknowledge" type="checkbox" ${reviewEditor.acknowledge?'checked':''} onchange="updateReviewApproval('acknowledge',this.checked)"> 법령 적용·조정 방향·소득처분·유보 증감을 확인했습니다.</span></label></div><button class="btn primary" onclick="approveWorkpaperUI(${id})">검토 승인 · 유보원장 반영</button></div>`:`<p class="notice">${esc(paperStatus(w))} · 검토자 ${esc(w.reviewer||'미기록')} · ${esc(w.approved_at||'')}<br>${esc(w.review_note||'')}</p>${w.status==='APPROVED'&&!reviewClosing?.close?`<button class="btn danger" onclick="cancelWorkpaperUI(${id})">승인 취소 · 원장 역분개</button>`:''}`}</fieldset></article>`;
 if(!editable)reviewEditor.rows=rows;
 renderReviewRows(editable);$('workpaperDetail').scrollIntoView?.({behavior:'smooth',block:'start'});
}
function renderReviewRows(editable=true){
 const w=currentWorkpapers.find(x=>x.id===reviewEditor?.id);if(!w)return;const a=w.candidate,rows=reviewEditor.rows;
 const options=(values,selected)=>values.map(([value,label])=>`<option value="${esc(value)}" ${String(value)===String(selected)?'selected':''}>${esc(label)}</option>`).join('');
 if(!editable){$('reviewRowsHost').innerHTML=rows.length?table(['대상 자산','조정 원인','익금·손금','소득처분','금액','유보 증감','승인 근거'],rows.map((r,i)=>[esc(assetLabel(getAsset(r.asset_id)||{id:r.asset_id,asset_name:'보존 자산'})),esc(r.label||'승인 조정 '+(i+1)),esc(r.direction),esc(r.disposition),esc(RM.display(r.amount,RM.scaleOf(a))),esc(RM.display(r.reserve_movement||0,RM.scaleOf(a))),esc(r.legal_basis)])).replace('<table>','<table class="review-approved">'):'<p class="review-empty">기록된 조정 행이 없습니다.</p>';updateReviewTotals();return;}
 $('reviewRowsHost').innerHTML=`<div class="table-wrap"><table class="review-editor"><thead><tr>${['대상 자산 · 조정 원인','익금·손금','소득처분','금액 · 유보 증감','검토 근거','행 관리'].map(x=>'<th>'+x+'</th>').join('')}</tr></thead><tbody>${rows.map((r,i)=>`<tr><td><select aria-label="${i+1}행 대상 자산" onchange="updateReviewRow(${i},'asset_id',this.value)">${options(db.assets.map(a=>[a.id,assetLabel(a)]),r.asset_id||a.asset_id)}</select><input maxlength="200" aria-label="${i+1}행 조정 원인" value="${esc(r.label||'')}" oninput="updateReviewRow(${i},'label',this.value)"></td><td><select aria-label="${i+1}행 익금 손금" onchange="updateReviewRow(${i},'direction',this.value)">${options(['익금산입','익금불산입','손금산입','손금불산입'].map(x=>[x,x]),r.direction)}</select></td><td><select aria-label="${i+1}행 소득처분" onchange="updateReviewRow(${i},'disposition',this.value)">${options(['유보','△유보','기타','기타사외유출','상여','배당'].map(x=>[x,x]),r.disposition)}</select></td><td><input aria-label="${i+1}행 금액" type="number" min="${RM.scaleOf(a)===0?1:0.01}" step="${RM.scaleOf(a)===0?1:0.01}" value="${esc(r.amount)}" oninput="updateReviewRow(${i},'amount',this.value)"><small>유보 증감 <output id="reviewMovement${i}">${esc(r.reserve_movement||'0')}</output></small></td><td><textarea aria-label="${i+1}행 검토 근거" oninput="updateReviewRow(${i},'legal_basis',this.value)">${esc(r.legal_basis||'')}</textarea></td><td class="row-actions"><button class="btn" onclick="removeReviewRow(${i})" aria-label="${i+1}행 삭제">삭제</button></td></tr>`).join('')}</tbody></table></div><button class="btn" onclick="addReviewRow()">조정 행 추가</button><p class="hint">유보 증감은 금액과 소득처분으로 계산합니다. 유보는 +, △유보는 −, 그 외는 0원입니다. 자동 규칙의 행을 삭제·상계하면 서버에서 승인이 차단될 수 있습니다.</p>`;
 updateReviewTotals();
}
function updateReviewApproval(field,value){if(reviewEditor&&!reviewEditor.submitting){reviewEditor[field]=value;reviewEditor.dirty=true;reviewEdits++}}
function updateReviewRow(index,key,value){
 if(reviewEditor?.submitting)return;
 const r=reviewEditor?.rows[index];if(!r)return;r[key]=value;
 if(['amount','disposition'].includes(key)){r.reserve_movement=RM.movement(r);$('reviewMovement'+index).textContent=r.reserve_movement;}
 reviewEditor.dirty=true;reviewEdits++;updateReviewTotals();
}
function addReviewRow(){if(!reviewEditor||reviewEditor.submitting)return;const w=currentWorkpapers.find(x=>x.id===reviewEditor.id);if(reviewEditor.rows.length>=20)return notify('조정 행은 최대 20개입니다.');reviewEditor.rows.push({asset_id:String(w.candidate.asset_id),label:'사용자 검토 조정',direction:'익금산입',disposition:'기타',amount:'',reserve_movement:'0',legal_basis:''});reviewEditor.dirty=true;reviewEdits++;renderReviewRows()}
function removeReviewRow(index){if(!reviewEditor||reviewEditor.submitting)return;reviewEditor.rows.splice(index,1);reviewEditor.dirty=true;reviewEdits++;renderReviewRows()}
function updateReviewTotals(){
 const w=currentWorkpapers.find(x=>x.id===reviewEditor?.id);if(!w)return;
 try{const t=RM.totals(reviewEditor.rows,w.candidate);$('reviewTotals').innerHTML=`<div class="review-totals"><div>과세소득 조정 합계 <b>${esc(RM.display(t.income,t.scale))}</b> / 서버 기준 ${esc(RM.display(w.candidate.candidate_adjustment,t.scale))} · ${t.incomeMatch?'일치':'불일치'}</div><div>유보 증감 합계 <b>${esc(RM.display(t.reserve,t.scale))}</b> · 자산별 대사 ${t.reserveMatch?'일치':'불일치'}</div></div><div id="reviewRowErrors" class="${t.errors.length?'alert':'hint'}">${t.errors.map(esc).join('<br>')||'합계는 산술 대사입니다. 승인 시 서버가 조정 방향·소득처분·법령 적용과 실제 원장 전기를 다시 확인합니다.'}</div>`;
 }catch(e){$('reviewTotals').textContent=e.message}
}
async function approveWorkpaperUI(id){
 if(pendingApprovals.has(id))return;
 let editor;
 try{
  if(!reviewEditor||reviewEditor.id!==id||reviewEditor.revision!==dbRevision||reviewEditor.year!==reviewYearValue())throw Error('원천·사업연도가 변경되었습니다. 검토 현황을 다시 불러오세요.');
  const w=currentWorkpapers.find(x=>x.id===id);if(stalePaper(w)||w.status!=='DRAFT'||reviewClosing?.close)throw Error('현재 승인 가능한 회차가 아닙니다.');
  const t=RM.totals(reviewEditor.rows,w.candidate);if(t.errors.length)throw Error(t.errors.join(' / '));if(!t.incomeMatch||!t.reserveMatch)throw Error('과세소득·자산별 유보 합계를 서버 기준에 맞춰 확인하세요.');
  const reviewer=$('wpReviewer').value.trim(),note=$('wpNote').value.trim();if(!reviewer||note.length<10||!$('wpAcknowledge').checked)throw Error('검토자·10자 이상 의견·검토 확인이 필요합니다.');
  if(!confirm('검토한 조정 행을 승인하고 유보 해당 행을 원장에 반영할까요?'))return;
  editor=reviewEditor;const year=editor.year;
  const adjustments=editor.rows.map(r=>({...r}));
  pendingApprovals.add(id);editor.submitting=true;showWorkpaperUI(id);
  const out=await dbRequest('/api/workpapers/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,reviewer,note,acknowledge:true,adjustments})});
  editor.submitting=false;
  if(reviewEditor===editor&&reviewYearValue()===year){
   editor.dirty=false;
   if(await refreshReviewUI({expectedEditor:editor}))showWorkpaperUI(id);
  }
  notify(year+'사업연도 승인 완료 · 유보원장 반영 '+out.reserve_posted+'건');
 }catch(e){notify('승인 실패: '+e.message)}
 finally{pendingApprovals.delete(id);if(editor){editor.submitting=false;if(reviewEditor===editor)showWorkpaperUI(id);}}
}
async function cancelWorkpaperUI(id){
 const reviewer=prompt('취소 담당자 이름');if(!reviewer)return;const reason=prompt('취소·정정 사유 (10자 이상)');if(!reason)return;
 if(!confirm('원본 승인을 보존하고 유보 전기를 역분개할까요?'))return;
 const editor=reviewEditor,year=reviewYearValue();
 try{
  const out=await dbRequest('/api/workpapers/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,reviewer,reason})});
  if(reviewEditor===editor&&reviewYearValue()===year){
   if(editor)editor.dirty=false;
   if(await refreshReviewUI({expectedEditor:editor}))showWorkpaperUI(id);
  }
  notify(year+'사업연도 승인 취소 · 역분개 '+out.reversed_entries+'건. 새 회차를 생성하여 검토하세요.');
 }catch(e){notify('승인 취소 실패: '+e.message)}
}
function renderClosingReview(){
 if(!reviewClosing)return;const out=reviewClosing;$('closeForm').hidden=!!out.close;const positions=calculation(fiscalEndDate(reviewYear)).positions;
 const drafts=currentWorkpapers.filter(w=>w.status==='DRAFT'&&!stalePaper(w));
 const required=InvestmentEngine.workpaperCandidates(db,reviewYear),key=a=>[a.source_type,a.source_id,a.asset_id].join(':');
 const present=new Set(currentWorkpapers.filter(w=>['APPROVED','DRAFT'].includes(w.status)&&!stalePaper(w)).map(w=>key(w.candidate)));
 const missing=required.filter(a=>!present.has(key(a))).length;
 const keys=[...new Set([...Object.keys(out.opening),...Object.keys(out.movements),...Object.keys(out.ending)])];
 const compare=positions.map(p=>{
  const aid=String(p.asset.id),actual=Object.entries(out.ending).filter(([k])=>k.split('|')[0]===aid).reduce((n,[,v])=>n+RM.units(v),0n),expected=RM.units(p.tax)-RM.units(p.book);
  const pending=drafts.flatMap(w=>w.candidate.allocation_effects||[]).filter(e=>String(e.assetId)===aid).reduce((n,e)=>n+RM.units(e.reserveChange),0n);
  return [esc(p.asset.asset_name||p.asset.stock_code),esc(p.asset.classification),esc(RM.display(RM.decimal(expected))),esc(RM.display(RM.decimal(actual))),esc(RM.display(RM.decimal(actual-expected))),esc(RM.reconciliation(RM.decimal(actual),RM.decimal(expected),RM.decimal(pending),missing===0))];
 });
 $('closingResult').innerHTML=`<b>${reviewYear} 사업연도 · ${out.close?'결산 확정':'결산 미확정'}</b><p>승인 대기 ${drafts.length}건 · 검증표 미작성 ${missing}건 · 직전 결산 ${out.prior_closed?'확정':'미확정'}</p>${table(['종목 · 분류 · 유보항목','기초','당기 증감','기말'],keys.map(k=>[reserveItemLabel(k),esc(RM.display(out.opening[k]||0)),esc(RM.display(out.movements[k]||0)),esc(RM.display(out.ending[k]||0))]))}${table(['종목','분류','계산 유보','승인 원장','원장−계산','차이 상태'],compare)}<p class="hint">‘승인 대기 차이’는 현재 미승인 검증표의 자산별 증감이 차이와 정확히 일치할 때 표시합니다. 승인 전에는 결산할 수 없습니다. 그 외 차이는 원천·승인·원장 이력을 확인하세요.</p>`;
}
async function refreshClosingUI(){return refreshReviewUI()}
function renderValidationResult(out){
 resetIssueReview();
 $('validationResults').innerHTML=table(['등급','확인할 내용','대상','기대값','실제값','검토 기록'],out.issues.map(x=>[esc(({ERROR:'오류',WARNING:'경고',INFO:'안내'})[x.severity]||x.severity),`<b>${esc(x.title)}</b><br>${esc(x.detail)}<details><summary>검사 코드</summary>${esc(x.code)}</details>`,esc(x.ref),esc(x.expected??'—'),esc(x.actual??'—'),`<button class="btn" onclick="reviewIssue('${encodeURIComponent(x.code+'|'+(x.ref||''))}')">처리 기록</button>`]));
}
function reviewIssue(encoded){
 if(!lastValidationRun)return notify('현재 사업연도로 검증을 먼저 실행하세요.');
 const key=decodeURIComponent(encoded),context={run:lastValidationRun,year:reviewYearValue(),revision:dbRevision};
 issueReviewContext=context;
 $('issueReviewEditor').innerHTML=`<div class="review-approval"><h4>검토·처리 기록</h4><p>${esc(key)}</p><label>처리 상태<select id="issueReviewStatus"><option value="REVIEWING">검토 중</option><option value="OPEN">미처리</option><option value="RESOLVED">수정 완료</option><option value="ACCEPTED">근거 확인</option></select></label><label>처리 근거<textarea id="issueReviewReason"></textarea></label><button class="btn" id="issueReviewSave">기록 저장</button></div>`;
 $('issueReviewSave').onclick=async()=>{try{
  if(issueReviewContext!==context||lastValidationRun!==context.run||reviewYearValue()!==context.year||dbRevision!==context.revision)throw Error('검증 실행이 변경되었습니다. 현재 결과에서 처리 기록을 다시 여세요.');
  await dbRequest('/api/validation/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({issue_key:key,status:$('issueReviewStatus').value,reason:$('issueReviewReason').value,run_id:context.run})});await loadIssueReviews();if(issueReviewContext===context)resetIssueReview();notify('검토 기록 저장 완료. 오류 해소는 재검증으로 확인하세요.')}catch(e){notify('검토 기록 실패: '+e.message)}};
}
window.addEventListener('beforeunload',event=>{if(reviewEditor?.dirty){event.preventDefault();event.returnValue=''}});
