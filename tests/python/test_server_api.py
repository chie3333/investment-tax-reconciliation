"""Exercise the HTTP handler without opening sockets or touching user databases."""
import io
import json
import unittest
from unittest.mock import patch

from app.server.server import Handler
from app.server.calculation import calculate_year
from tests.python import test_server_controls as control_helpers


class RequestSocket:
    def __init__(self, request):
        self.input=io.BytesIO(request)
        self.output=bytearray()
    def makefile(self,*args,**kwargs):
        return self.input
    def sendall(self,data):
        self.output.extend(data)


class ServerApiTests(unittest.TestCase):
    def setUp(self):
        control_helpers.ServerControlTests.setUp(self)

    def request(self,path,payload=None):
        body=json.dumps(payload).encode() if payload is not None else b''
        method='POST' if payload is not None else 'GET'
        raw=f'{method} {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n'.encode()+body
        sock=RequestSocket(raw)
        with patch.object(Handler,'log_message'):
            Handler(sock,('127.0.0.1',12345),object())
        head,data=bytes(sock.output).split(b'\r\n\r\n',1)
        return int(head.split()[1]),json.loads(data)

    def test_generate_validate_approve_and_close_through_api(self):
        calculated=calculate_year(self.state,2026)
        code,result=self.request('/api/workpapers/generate',dict(year=2026,expected_revision=1))
        self.assertEqual(code,200);self.assertEqual(result['created'],2)
        code,result=self.request('/api/validate',dict(year=2026,expected_revision=1,calculated={'positions':calculated['positions']}))
        self.assertEqual(code,200);self.assertEqual(result['summary']['ERROR'],0)
        code,result=self.request('/api/workpapers?year=2026')
        self.assertEqual(code,200)
        for wp in result['items']:
            rows=control_helpers.ServerControlTests.adjustments(self,wp['candidate'])
            code,approved=self.request('/api/workpapers/approve',dict(id=wp['id'],reviewer='API 검토자',note='실제 HTTP 경로의 계산 및 원장 검토',adjustments=rows,acknowledge=True))
            self.assertEqual(code,200,approved)
        code,error=self.request('/api/closing/close',dict(year=2026,reviewer='API 검토자',note='최종 검토',acknowledge_warnings=True,positions=[dict(assetId=1,qty=0,book=1,tax=1)]))
        self.assertEqual(code,400);self.assertIn('재계산 불일치',error['error'])
        code,closed=self.request('/api/closing/close',dict(year=2026,reviewer='API 검토자',note='최종 검토',acknowledge_warnings=True,positions=calculated['positions']))
        self.assertEqual(code,200,closed);self.assertTrue(closed['close'])

    def test_revision_and_browser_mismatch_are_reported(self):
        code,error=self.request('/api/validate',dict(year=2026,expected_revision=0))
        self.assertEqual(code,409)
        code,result=self.request('/api/validate',dict(year=2026,expected_revision=1,calculated={'positions':[]}))
        self.assertEqual(code,200)
        self.assertIn('SERVER_CALC_MISMATCH',[i['code'] for i in result['issues']])
        code,error=self.request('/api/workpapers/generate',dict(year=2026,expected_revision=1,candidates=[]))
        self.assertEqual(code,400);self.assertIn('후보 누락',error['error'])
