import os, tempfile, unittest, importlib
from pathlib import Path
from unittest.mock import patch

class ClosingTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  from app.server import storage, closing
  self.storage=storage;self.closing=closing
  self.old=storage.DB_PATH;storage.DB_PATH=Path(self.temp.name)/'test.sqlite3';self.addCleanup(setattr,storage,'DB_PATH',self.old)
  self.state={'master':[],'assets':[{'id':1,'stock_code':'005930'}],'trades':[],'valuations':[],'adjustments':[],'taxPolicyYears':[],'fiscalEndMonth':12}
  self.storage.save_state(self.state,0)
  with self.storage.connect() as c:
   c.execute('CREATE TABLE validation_runs (id INTEGER PRIMARY KEY AUTOINCREMENT,recorded_at TEXT NOT NULL,source_revision INTEGER NOT NULL,fiscal_year INTEGER NOT NULL,result_json TEXT NOT NULL)')
 def valid(self,year,errors=0,warnings=0):
  import json
  with self.storage.connect() as c:c.execute('INSERT INTO validation_runs(recorded_at,source_revision,fiscal_year,result_json) VALUES(?,?,?,?)',('now',1,year,json.dumps({'summary':{'ERROR':errors,'WARNING':warnings}})))
 def test_carryforward(self):
  self.valid(2025)
  with self.assertRaisesRegex(ValueError,'비활성화'):self.closing.add_entry(2025,1,'FVTPL_VAL','val-1','-300','평가차이')
  with self.assertRaisesRegex(ValueError,'대사 자료'):self.closing.close_year(2025,'reviewer','검토 완료')
 def test_blocks_errors(self):
  self.valid(2026,errors=1)
  with self.assertRaisesRegex(ValueError,'오류'):self.closing.close_year(2026,'reviewer','검토',positions=[{'assetId':1,'qty':0,'book':0,'tax':0}])
 def test_blocks_warnings_without_approval(self):
  self.valid(2026,warnings=1)
  with self.assertRaisesRegex(ValueError,'경고'):self.closing.close_year(2026,'reviewer','검토',positions=[{'assetId':1,'qty':0,'book':0,'tax':0}])
 def test_closed_period_immutable(self):
  self.valid(2026);self.closing.close_year(2026,'reviewer','검토',positions=[{'assetId':1,'qty':0,'book':0,'tax':0}])
  changed={**self.state,'trades':[{'id':1,'date':'2026-04-01','assetId':1,'type':'BUY','qty':1,'price':10,'amount':10}]}
  with self.assertRaisesRegex(ValueError,'확정'):self.storage.save_state(changed,1)
 def test_closed_reserve_immutable(self):
  self.valid(2026);self.closing.close_year(2026,'reviewer','검토',positions=[{'assetId':1,'qty':0,'book':0,'tax':0}])
  with self.assertRaisesRegex(ValueError,'비활성화'):self.closing.add_entry(2026,1,'FVTPL','x',1,'수정')
 def test_closed_fvoci_policy_immutable(self):
  self.valid(2026);self.closing.close_year(2026,'reviewer','검토',positions=[{'assetId':1,'qty':0,'book':0,'tax':0}])
  changed={**self.state,'fvociPolicy':{'disposal':'RETAIN','reviewer':'검토자','reason':'회사 정책'}}
  with self.assertRaisesRegex(ValueError,'fvociPolicy'):self.storage.save_state(changed,1)
 def test_fvoci_policy_saved_and_audited(self):
  policy={'disposal':'RETAIN','reviewer':'검토자','reason':'회사 정책','history':[]}
  self.storage.save_state({**self.state,'fvociPolicy':policy},1)
  self.assertEqual(self.storage.read_state()['state']['fvociPolicy'],policy)
  events=self.storage.read_audit()
  self.assertTrue(any(e['entity']=='settings' and e['entity_key']=='fvociPolicy' for e in events))
if __name__=='__main__':unittest.main()
