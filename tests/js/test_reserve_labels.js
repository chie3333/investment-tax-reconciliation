const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '../../frontend/index.html'), 'utf8');
const script = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)]
 .map(m => m[1]).find(s => s.includes('function reserveItemLabel('));
const assets = [{id:1,asset_name:'삼성전자',classification:'FVOCI'},
 {id:2,asset_name:'<img src=x onerror=alert(1)>',classification:'FVPL'}];
const context = vm.createContext({getAsset:id=>assets.find(a=>a.id===Number(id)),
 esc:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))});
new vm.Script(script).runInContext(context);
const label = key=>context.reserveItemLabel(key);
assert.equal(label('1|FVOCI_TRADE'),'삼성전자 · FVOCI · 처분 등 거래 관련');
assert.equal(label('1|FVOCI_VALUATION'),'삼성전자 · FVOCI · 평가 관련');
assert.equal(label('1|FVPL_TRADE'),'삼성전자 · FVPL · 처분 등 거래 관련');
assert.equal(label('99|FVOCI_VALUATION'),'미등록 종목 #99 · FVOCI · 평가 관련');
assert.equal(label('1|LEGACY_CODE'),'삼성전자 · FVOCI · 기타 유보항목');
assert.equal(label('99'),'미등록 종목 #99 · 분류 미확인 · 기타 유보항목');
assert.ok(label('2|FVPL_TRADE').includes('&lt;img'));
assert.ok(!label('2|FVPL_TRADE').includes('<img'));
for (const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
console.log('Reserve labels and inline script syntax: passed');
