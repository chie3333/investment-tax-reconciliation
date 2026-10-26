"""Legacy one-time migration retained for development-history reproducibility."""
from pathlib import Path

p=Path(__file__).resolve().parents[1] / 'frontend' / 'index.html'
s=p.read_text()
extra='''
<style>
.krx-line{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:7px}.krx-line .btn{font-size:12px}.krx-status{font-size:12px;color:var(--muted,#64748b)}.krx-results{margin-top:12px}.krx-results table{width:100%}.krx-results .btn{font-size:12px}
</style>
<script>
// KRX daily closing-price lookup: the key is kept on the local Python server, never in browser storage.
let krxLastBatch=[];
async function krxRequest(day,codes,previous){
 const q=new URLSearchParams({date:day,codes:codes.join(','),previous:previous?'1':'0'});
 let res;try{res=await fetch('/api/krx/prices?'+q.toString())}catch(e){throw Error('로컬 Python 서버에 연결할 수 없습니다. python3 -m app.server 로 실행하세요.')}
 let body;try{body=await res.json()}catch(e){throw Error('시세 API가 연결되지 않았습니다. HTML 파일을 직접 연 경우 로컬 서버로 실행하세요.')}
 if(!res.ok)throw Error(body.error||'KRX 시세 조회 실패');return body;
}
function krxMsg(id,msg){const el=document.getElementById(id);if(el)el.textContent=msg}
async function lookupTradeKrx(){
 const code=document.getElementById('tStock').value.trim(),day=document.getElementById('tDate').value;
 if(!/^\\d{6}$/.test(code)||!day)return krxMsg('krxTradeStatus','종목코드(6자리)와 거래일자를 입력하세요.');
 krxMsg('krxTradeStatus','KRX 종가 조회 중…');
 try{const d=await krxRequest(day,[code],document.getElementById('krxPrevious').checked),p=d.prices[code];
 if(!p)return krxMsg('krxTradeStatus','해당 날짜 또는 직전 7일 이내 시세가 없습니다. 종목코드와 상장 여부를 확인하세요.');
 document.getElementById('tPrice').value=p.price;
 krxMsg('krxTradeStatus',`${p.price_date} 종가 ${Number(p.price).toLocaleString('ko-KR')}원 입력됨 · ${p.name} · 실제 체결단가와 다르면 수정하세요.`);
 }catch(e){krxMsg('krxTradeStatus',e.message)}
}
async function lookupValuationKrx(){
 const asset=getAsset(document.getElementById('vAsset').value),day=document.getElementById('vDate').value;
 if(!asset||!day)return krxMsg('krxValStatus','평가 대상 자산과 평가일을 선택하세요.');
 krxMsg('krxValStatus','KRX 종가 조회 중…');
 try{const d=await krxRequest(day,[asset.stock_code],document.getElementById('krxPrevious').checked),p=d.prices[asset.stock_code];
 if(!p)return krxMsg('krxValStatus','조회 가능한 종가가 없습니다.');
 document.getElementById('vPrice').value=p.price;
 document.getElementById('vPrice').dispatchEvent(new Event('input',{bubbles:true}));
 krxMsg('krxValStatus',`${p.price_date} 종가 ${Number(p.price).toLocaleString('ko-KR')}원 입력됨 · 평가 저장 버튼을 눌러야 장부에 반영됩니다.`);
 }catch(e){krxMsg('krxValStatus',e.message)}
}
async function lookupAllKrx(){
 const day=document.getElementById('vDate').value,output=document.getElementById('krxBatch');
 if(!day){output.textContent='평가일을 선택하세요.';return}
 const active=db.assets.filter(a=>holdings(a.id,day)>0);
 const codes=[...new Set(active.map(a=>a.stock_code))];
 if(!codes.length){output.textContent='해당 날짜에 보유 중인 종목이 없습니다.';return}
 output.textContent='보유 종목의 KRX 종가를 조회하고 있어요…';
 try{const data=await krxRequest(day,codes,document.getElementById('krxPrevious').checked);
 krxLastBatch=active.filter(a=>data.prices[a.stock_code]).map(a=>({asset:a,quote:data.prices[a.stock_code]}));
 let html='<table><thead><tr><th>종목</th><th>분류</th><th>보유수량</th><th>시세일</th><th>종가</th><th>상태</th></tr></thead><tbody>';
 for(const r of krxLastBatch){const qty=holdings(r.asset.id,day),exists=db.valuations.some(v=>v.assetId===r.asset.id&&v.date===day);
 html+=`<tr><td>${esc(r.asset.asset_name)}</td><td>${esc(r.asset.classification)}</td><td>${qty}</td><td>${r.quote.price_date}</td><td>${Number(r.quote.price).toLocaleString('ko-KR')}</td><td>${exists?'평가 기록 있음':'미확정'}</td></tr>`}
 html+='</tbody></table>';
 if(data.missing.length)html+=`<p class="hint">시세 없음: ${data.missing.map(esc).join(', ')}</p>`;
 html+='<p class="hint">조회된 종가는 아직 회계 장부에 반영되지 않았습니다. 휴장일 직전 종가 사용 시 실제 평가일과 시세일을 구분하여 검토하세요.</p>';
 html+='<button class="btn primary" type="button" onclick="confirmKrxBatch()">검토한 시세로 평가 기록 일괄 생성</button>';
 output.innerHTML=html;
 }catch(e){output.textContent=e.message}
}
function confirmKrxBatch(){
 const day=document.getElementById('vDate').value;
 if(!krxLastBatch.length)return;
 if(!confirm(`${day} 평가 기록을 ${krxLastBatch.length}개 자산에 생성할까요? 기존 동일 날짜 평가는 건너뜁니다.`))return;
 let count=0,skip=0;
 for(const {asset,quote} of krxLastBatch){
  if(db.valuations.some(v=>v.assetId===asset.id&&v.date===day)){skip++;continue}
  const qty=holdings(asset.id,day);if(qty<=0){skip++;continue}
  const before=calculationBeforeValuation(asset.id,day),fairValue=qty*quote.price;
  db.valuations.push({id:++db.ids.valuation,assetId:asset.id,date:day,qty,price:quote.price,before,fairValue,gain:fairValue-before,note:`KRX 종가 조회 · 시세일 ${quote.price_date} · ${quote.source}`});count++;
 }
 persist();krxLastBatch=[];document.getElementById('krxBatch').textContent=`${count}건 평가 저장, ${skip}건 건너뜀. 회계·세무 원장을 확인하세요.`;
}
function initKrxUI(){
 const price=document.getElementById('tPrice');const val=document.getElementById('vPrice');if(!price||!val)return;
 price.insertAdjacentHTML('afterend','<div class="krx-line"><button class="btn" type="button" onclick="lookupTradeKrx()">KRX 취득일 종가 조회</button><span class="krx-status" id="krxTradeStatus">실제 체결단가는 직접 확인해야 합니다.</span></div>');
 val.insertAdjacentHTML('afterend','<div class="krx-line"><button class="btn" type="button" onclick="lookupValuationKrx()">KRX 평가일 종가 조회</button><span class="krx-status" id="krxValStatus">조회 후 평가 기록 저장</span></div>');
 const vpanel=document.querySelector('#valuations .panel');vpanel.insertAdjacentHTML('beforeend','<div class="separator"></div><h3>KRX 보유종목 일괄 시세 조회</h3><p class="hint">평가일에 보유한 종목을 조회하고, 확인 후 평가 기록을 일괄 생성합니다. 동일 종목의 분류별 보유분에는 같은 시장 종가를 적용합니다.</p><button type="button" class="btn" onclick="lookupAllKrx()">보유 종목 일괄 종가 조회</button><div id="krxBatch" class="krx-results"></div>');
 const check=document.createElement('div');check.className='notice';check.innerHTML='<label><input type="checkbox" id="krxPrevious" checked> 휴장일이면 최대 7일 전까지 직전 거래일 종가 검색</label><p class="hint">KRX 시세는 2010년 이후 일별 자료입니다. 가격 조회와 거래/평가 확정은 별도이며, API 인증키는 로컬 Python 서버 환경변수에 보관합니다.</p>';
 vpanel.insertAdjacentElement('afterbegin',check);
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initKrxUI);else initKrxUI();
</script>
'''
assert '</body></html>' in s
p.write_text(s.replace('</body></html>',extra+'</body></html>'))
