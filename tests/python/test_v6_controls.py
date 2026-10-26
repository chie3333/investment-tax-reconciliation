import tempfile, unittest
from pathlib import Path
from app.server import storage, closing, workpapers
from app.server.reconciliation import run_validation


class V6Controls(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  old=storage.DB_PATH;storage.DB_PATH=Path(self.tmp.name)/'test.sqlite3'
  self.addCleanup(setattr,storage,'DB_PATH',old)
  self.state={'master':[],'assets':[{'id':1,'stock_code':'005930','classification':'FVOCI'}],
   'trades':[{'id':1,'assetId':1,'date':'2026-01-02','type':'BUY','qty':10,'price':100,'amount':1000},
             {'id':2,'assetId':1,'date':'2026-10-02','type':'SELL','qty':10,'price':120,'amount':1200}],
   'valuations':[{'id':1,'assetId':1,'date':'2026-06-30','price':110,'qty':10,'fairValue':1100}],
   'adjustments':[],'taxPolicyYears':[],'fiscalEndMonth':12}
  storage.save_state(self.state,0)
  workpapers.generate(2026,1)
  self.id=next(w['id'] for w in workpapers.list_workpapers(2026)['items'] if w['candidate']['event']=='SELL')
  self.val_id=next(w['id'] for w in workpapers.list_workpapers(2026)['items'] if w['candidate']['event']=='VALUATION')
  self.positions=[{'assetId':1,'qty':0,'book':0,'tax':0}]
 def approve_sale(self):
  rows=[dict(direction='익금산입',disposition='기타',amount='100',reserve_movement='0',legal_basis='처분 검토'),
        dict(direction='익금산입',disposition='유보',amount='100',reserve_movement='100',legal_basis='장부 차이 환입')]
  return workpapers.approve(self.id,'reviewer','원천 자료와 유보 환입 확인함',rows,True)
 def approve_valuation(self):
  rows=[dict(direction='익금산입',disposition='기타',amount='100',reserve_movement='0',legal_basis='OCI 검토'),
        dict(direction='손금산입',disposition='△유보',amount='100',reserve_movement='-100',legal_basis='평가 차이 검토')]
  return workpapers.approve(self.val_id,'reviewer','평가 근거와 장부 차이 확인함',rows,True)
 def test_manual_entry_blocked(self):
  with self.assertRaisesRegex(ValueError,'비활성화'):closing.add_entry(2026,1,'X','x',1,'memo')
 def test_cancel_reverses_without_deleting_and_regenerate(self):
  self.approve_sale()
  self.assertEqual(closing.summary(2026)['ending']['1|FVOCI_TRADE'],'100')
  workpapers.cancel_approval(self.id,'reviewer','처분 근거를 다시 검토하기 위함')
  summary=closing.summary(2026)
  self.assertEqual(summary['ending'],{})
  self.assertEqual(len(summary['entries']),2)
  self.assertEqual(workpapers.generate(2026,1)['created'],1)
  self.assertEqual(workpapers.generate(2026,1)['created'],0)
  items=workpapers.list_workpapers(2026)['items']
  self.assertEqual(items[-1]['source_key'],'trade:2:1:R2')
  with self.assertRaisesRegex(ValueError,'취소 가능한'):workpapers.cancel_approval(self.id,'reviewer','중복 취소를 시도하는 테스트')
 def test_close_blocks_unapproved_and_unreconciled(self):
  run_validation(2026,1)
  with self.assertRaisesRegex(ValueError,'미승인'):closing.close_year(2026,'reviewer','검토',True,self.positions)
  self.approve_sale();self.approve_valuation()
  # Balanced but incorrect browser book/tax values used to bypass the close gate.
  with self.assertRaisesRegex(ValueError,'재계산 불일치'):
   closing.close_year(2026,'reviewer','검토',True,[{'assetId':1,'qty':0,'book':10,'tax':10}])
 def test_close_succeeds_after_reapproved_and_reconciled(self):
  run_validation(2026,1)
  self.approve_valuation();self.approve_sale()
  workpapers.cancel_approval(self.id,'reviewer','원천 근거 재확인 후 재승인함')
  workpapers.generate(2026,1)
  self.id=workpapers.list_workpapers(2026)['items'][-1]['id']
  self.approve_sale()
  out=closing.close_year(2026,'reviewer','최종 근거 검토 완료',True,self.positions)
  self.assertTrue(out['close'])
  self.assertEqual(out['ending'],{'1|FVOCI_TRADE':'100','1|FVOCI_VALUATION':'-100'})
  with self.assertRaisesRegex(ValueError,'확정'):workpapers.cancel_approval(self.id,'reviewer','확정 후 취소를 시도하는 테스트')
