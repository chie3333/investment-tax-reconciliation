"""Local KRX price proxy for the investment tax prototype (stdlib only)."""
import json
import os
import re
from datetime import date, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from .storage import read_state, save_state, read_audit, save_validation, read_validation_runs, save_issue_review, read_issue_reviews
from .validation_engine import validate
from .reconciliation import run_validation
from .closing import summary as closing_summary, add_entry as add_reserve_entry, close_year
from .workpapers import generate as generate_workpapers, list_workpapers, approve as approve_workpaper, cancel_approval as cancel_workpaper
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse, urlencode
from urllib.request import Request, urlopen

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = REPOSITORY_ROOT / 'frontend'
KRX_BASE = 'https://data-dbg.krx.co.kr/svc/apis/sto/'
MARKETS = ('stk_bydd_trd', 'ksq_bydd_trd', 'knx_bydd_trd')


def daily_rows(day):
    key = os.environ.get('KRX_API_KEY', '').strip()
    if not key:
        raise RuntimeError('KRX_API_KEY 환경변수가 없습니다. KRX 인증키와 서비스별 이용 승인이 필요합니다.')
    rows = []
    for endpoint in MARKETS:
        url = KRX_BASE + endpoint + '?' + urlencode({'basDd': day.strftime('%Y%m%d')})
        req = Request(url, headers={'AUTH_KEY': key, 'Accept': 'application/json'})
        try:
            with urlopen(req, timeout=12) as resp:
                payload = json.load(resp)
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise RuntimeError(f'KRX 인증/서비스 승인 오류 ({exc.code}, {endpoint}). KRX에서 해당 주식 API의 이용 승인을 확인하세요.') from exc
            raise RuntimeError(f'KRX API 응답 오류 ({exc.code}, {endpoint})') from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f'KRX 네트워크 연결 실패 ({endpoint}): {exc}') from exc
        if not isinstance(payload, dict) or not isinstance(payload.get('OutBlock_1'), list):
            raise RuntimeError(f'KRX 응답 형식을 확인할 수 없습니다 ({endpoint}).')
        rows.extend(payload['OutBlock_1'])
    return rows


def prices_for_date(day, codes, backtrack=False):
    # Limit historical lookup to the preceding 7 calendar days, not arbitrary old prices.
    days = (day - timedelta(days=i) for i in range(8 if backtrack else 1))
    for target in days:
        if target > date.today() or target < date(2010, 1, 4):
            continue
        rows = daily_rows(target)
        found = {}
        for r in rows:
            code = str(r.get('ISU_CD', '')).strip()
            # Some feeds expose 12-digit ISIN-like codes; exact 6-digit match only.
            if code not in codes:
                continue
            raw = str(r.get('TDD_CLSPRC', '')).replace(',', '').strip()
            try:
                price = float(raw)
            except ValueError:
                continue
            if price < 0:
                continue
            found[code] = {'stock_code': code, 'name': r.get('ISU_NM', ''), 'market': r.get('MKT_NM', ''), 'price': price, 'price_date': target.isoformat(), 'source': 'KRX 일별매매정보 · 종가'}
        if not backtrack or all(c in found for c in codes):
            return found
        if found:
            # Different symbols may have different valid preceding dates; handle below.
            result = found
            for prior in range((day-target).days + 1, 8):
                pday = day - timedelta(days=prior)
                for row in daily_rows(pday):
                    c = str(row.get('ISU_CD', '')).strip()
                    if c in codes and c not in result:
                        try: price = float(str(row.get('TDD_CLSPRC', '')).replace(',', ''))
                        except ValueError: continue
                        result[c] = {'stock_code':c,'name':row.get('ISU_NM',''),'market':row.get('MKT_NM',''),'price':price,'price_date':pday.isoformat(),'source':'KRX 일별매매정보 · 종가'}
                if all(c in result for c in codes): break
            return result
    return {}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_ROOT), **kwargs)

    def send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path=urlparse(self.path).path
        if path in ('/api/workpapers/generate','/api/workpapers/approve','/api/workpapers/cancel'):
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size<=0 or size>500_000:return self.send_json({'error':'요청 크기 오류'},413)
                payload=json.loads(self.rfile.read(size))
                if path.endswith('/generate'):
                    result=generate_workpapers(payload['year'],payload['expected_revision'],payload.get('candidates'))
                elif path.endswith('/cancel'):
                    result=cancel_workpaper(payload['id'],payload['reviewer'],payload['reason'])
                else:
                    result=approve_workpaper(payload['id'],payload['reviewer'],payload['note'],payload['adjustments'],payload.get('acknowledge'))
                return self.send_json(result)
            except (ValueError,KeyError,TypeError) as exc:return self.send_json({'error':str(exc)},400)
        if path in ('/api/reserves/entry','/api/closing/close'):
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size<=0 or size>10000:return self.send_json({'error':'요청 크기 오류'},413)
                payload=json.loads(self.rfile.read(size))
                if path=='/api/reserves/entry':
                    result=add_reserve_entry(payload['year'],payload['asset_id'],payload['adjustment_key'],payload['source_ref'],payload['amount'],payload['memo'])
                else:
                    result=close_year(payload['year'],payload['reviewer'],payload['note'],payload.get('acknowledge_warnings',False),payload.get('positions'))
                return self.send_json(result)
            except (ValueError,KeyError,TypeError) as exc:return self.send_json({'error':str(exc)},400)
        if urlparse(self.path).path == '/api/validation/review':
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size<=0 or size>5000:return self.send_json({'error':'요청 크기 오류'},413)
                payload=json.loads(self.rfile.read(size))
                return self.send_json(save_issue_review(payload['issue_key'],payload['status'],payload['reason'],int(payload['run_id'])))
            except (ValueError,KeyError,TypeError) as exc:return self.send_json({'error':str(exc)},400)
        if urlparse(self.path).path == '/api/validate':
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size<=0 or size>500_000: return self.send_json({'error':'요청 크기 오류'},413)
                payload=json.loads(self.rfile.read(size))
                current=read_state()
                if payload.get('expected_revision')!=current['revision']:
                    return self.send_json({'error':'저장 버전이 달라 검증을 중단했습니다.'},409)
                year=int(payload['year'])
                if year<1900 or year>2200:raise ValueError('사업연도 범위 오류')
                result=run_validation(year,payload['expected_revision'],payload.get('calculated'))
                return self.send_json(result)
            except (ValueError,TypeError,KeyError) as exc:return self.send_json({'error':str(exc)},400)
        if urlparse(self.path).path != '/api/state':
            return self.send_json({'error': '존재하지 않는 경로'}, 404)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size <= 0 or size > 8_000_000:
                return self.send_json({'error': '요청 크기가 허용 범위를 벗어났습니다.'}, 413)
            payload = json.loads(self.rfile.read(size))
            result = save_state(payload['state'], payload['expected_revision'])
            return self.send_json(result)
        except RuntimeError as exc:
            return self.send_json({'error': str(exc)}, 409)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            return self.send_json({'error': str(exc)}, 400)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/workpapers':
            try:return self.send_json(list_workpapers(int(parse_qs(parsed.query).get('year',['0'])[0])))
            except (ValueError,TypeError) as exc:return self.send_json({'error':str(exc)},400)
        if parsed.path == '/api/closing/summary':
            try:return self.send_json(closing_summary(int(parse_qs(parsed.query).get('year',['0'])[0])))
            except (ValueError,TypeError) as exc:return self.send_json({'error':str(exc)},400)
        if parsed.path == '/api/state':
            return self.send_json(read_state())
        if parsed.path == '/api/validation/reviews':
            return self.send_json(read_issue_reviews())
        if parsed.path == '/api/validation/history':
            return self.send_json({'runs':read_validation_runs()})
        if parsed.path == '/api/audit':
            return self.send_json({'events': read_audit()})
        if parsed.path == '/api/krx/status':
            return self.send_json({'configured': bool(os.environ.get('KRX_API_KEY','').strip()), 'provider':'KRX', 'note':'시세 조회만 제공. 거래 체결가격 아님.'})
        if parsed.path == '/api/krx/prices':
            try:
                params = parse_qs(parsed.query)
                ds = params.get('date', [''])[0]
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', ds):
                    raise ValueError('기준일은 YYYY-MM-DD 형식이어야 합니다.')
                day = date.fromisoformat(ds)
                if day > date.today(): raise ValueError('미래 날짜의 시세는 조회할 수 없습니다.')
                codes = [x.strip() for x in params.get('codes',[''])[0].split(',') if x.strip()]
                if not codes or len(codes) > 100 or any(not re.fullmatch(r'\d{6}', c) for c in codes):
                    raise ValueError('종목코드는 6자리 숫자이며 한 번에 최대 100개입니다.')
                backtrack = params.get('previous',['0'])[0] == '1'
                data = prices_for_date(day, set(codes), backtrack)
                return self.send_json({'requested_date': ds, 'prices': data, 'missing': sorted(set(codes)-set(data))})
            except (ValueError, RuntimeError) as exc:
                return self.send_json({'error': str(exc)}, 400)
            except Exception:
                return self.send_json({'error':'KRX 조회 중 예기치 못한 오류가 발생했습니다. 서버 로그를 확인하세요.'}, 502)
        if parsed.path.startswith('/data/') or parsed.path.startswith('/.') or parsed.path in ('/storage.py', '/server.py'):
            return self.send_json({'error': '접근할 수 없는 파일입니다.'}, 403)
        return super().do_GET()


def main():
    port = int(os.environ.get('PORT', '8000'))
    print(f'Open http://127.0.0.1:{port}/  (KRX key configured: {bool(os.environ.get("KRX_API_KEY"))})')
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()


if __name__ == '__main__':
    main()
