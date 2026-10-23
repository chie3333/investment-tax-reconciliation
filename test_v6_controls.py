import os, tempfile, unittest, json
from pathlib import Path

class V6Controls(unittest.TestCase):
 def setUp(self):
  import storage,closing,workpapers
  self.storage=storage;self.closing=closing;self.wp=workpapers
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.old=storage.DB_PATH;storage.DB_PATH=Path(self.tmp.name)/'t.sqlite';self.addCleanup(setattr,storage,'DB_PATH',self.old)
  self.state={'master':[],'assets':[{'id':1,'stock_code':'005930','classification':'FVOCI'}],
    'trades':[{'id':1,'assetId':1,'date':'2026-01-02','type':'BUY','qty':10,'price':100,'amount':1000},
              {'id':2,'assetId':1,'date':'2026-10-02','type':'SELL','qty':10,'price':120,'amount':1200}],
    'valuations':[],'adjustments':[],'taxPolicyYears':[],'fiscalEndMonth':12}
  storage.save_state(self.state,0)
  self.candidate={'asset_id':1,'classification':'FVOCI','source_type':'trade','source_id':2,'event':'SELL','date':'2026-10-02','book_pl':0,'tax_pl':200,'oci_change':0,'candidate_adjustment':200,'reserve_change':-100,'oci_equity_transfer':0}
  self.wp.generate(2026,1,[self.candidate]);self.id=self.wp.list_workpapers(2026)['items'][0]['id']
 def adjustments(self):return [dict(direction='익금산입',disposition='기타',amount='200',reserve_movement='0',legal_basis='검토 완료'),dict(direction='손금산입',disposition='△유보',amount='100',reserve_movement='-100',legal_basis='유보 환입 검토')]
 def approve(self):return self.wp.approve(self.id,'reviewer','근거 확인 후 승인함',self.adjustments(),True)
 def test_manual_entry_blocked(self):
  with self.assertRaisesRegex(ValueError,'비활성화'):self.closing.add_entry(2026,1,'X','x',1,'memo')
 def test_cancel_reverses_without_deleting_and_regenerate(self):
  self.approve()
  self.assertEqual(self.closing.summary(2026)['ending']['1|FVOCI_TRADE'],'-100')
  self.wp.cancel_approval(self.id,'reviewer','근거가 잘못되어 재검토함')
  summary=self.closing.summary(2026)
  self.assertEqual(summary['ending'],{})
  self.assertEqual(len(summary['entries']),2)
  self.assertEqual(self.wp.list_workpapers(2026)['items'][0]['status'],'CANCELLED')
  self.assertEqual(self.wp.generate(2026,1,[self.candidate])['created'],1)
  items=self.wp.list_workpapers(2026)['items']
  self.assertEqual(len(items),2)
  self.assertEqual(items[1]['source_key'],'trade:2:1:R2')
  with self.assertRaisesRegex(ValueError,'취소 가능한'):self.wp.cancel_approval(self.id,'reviewer','근거가 잘못되어 재검토함')
 def test_close_blocks_unapproved_and_unreconciled(self):
  self.storage.save_validation({'year':2026,'summary':{'ERROR':0,'WARNING':0}},1)
  positions=[{'assetId':1,'qty':0,'book':0,'tax':0}]
  with self.assertRaisesRegex(ValueError,'미승인'):self.closing.close_year(2026,'reviewer','검토',positions=positions)
  self.approve()
  with self.assertRaisesRegex(ValueError,'유보 대사'):self.closing.close_year(2026,'reviewer','검토',positions=positions)
  self.wp.cancel_approval(self.id,'reviewer','기존 환입 조정 재검토')
  with self.assertRaisesRegex(ValueError,'미작성'):self.closing.close_year(2026,'reviewer','검토',positions=positions)
 def test_close_succeeds_after_reapproved_and_reconciled(self):
  self.storage.save_validation({'year':2026,'summary':{'ERROR':0,'WARNING':0}},1)
  self.approve();self.wp.cancel_approval(self.id,'reviewer','원가 검토 후 수정 예정')
  self.wp.generate(2026,1,[self.candidate]);new_id=self.wp.list_workpapers(2026)['items'][-1]['id']
  rows=[dict(direction='익금산입',disposition='기타',amount='200',reserve_movement='0',legal_basis='근거 검토')]
  self.wp.approve(new_id,'reviewer','환입액 없음 검토',rows,True) if False else None
  # Approved reserve of +100 and -100 nets to zero; demonstrates the close gate.
  rows.extend([dict(direction='손금불산입',disposition='유보',amount='100',reserve_movement='100',legal_basis='발생 근거'),dict(direction='손금산입',disposition='△유보',amount='100',reserve_movement='-100',legal_basis='환입 근거')])
  self.wp.approve(new_id,'reviewer','잔액과 조정 확인함',rows,True)
  out=self.closing.close_year(2026,'reviewer','검토 완료',positions=[{'assetId':1,'qty':0,'book':0,'tax':0}])
  self.assertTrue(out['close'])
  with self.assertRaisesRegex(ValueError,'확정'):self.wp.cancel_approval(new_id,'reviewer','확정 후 취소 시도')

if __name__=='__main__':unittest.main()
