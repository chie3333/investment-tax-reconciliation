import unittest
from tests.python.test_validation_engine import ValidationTests
from app.server.validation_engine import validate

class ExpandedTests(unittest.TestCase):
 def setUp(self):
  self.s=ValidationTests().state()
 def codes(self, result):return {x['code'] for x in result['issues']}
 def test_duplicate_trade_fingerprint(self):
  self.s['trades'].append({**self.s['trades'][0], 'id':2})
  self.assertIn('QTY_DUPLICATE_TRADE',self.codes(validate(self.s,2027)))
 def test_stale_price(self):
  self.s['valuations']=[{'id':1,'assetId':1,'date':'2027-03-31','priceDate':'2027-03-01','qty':10,'price':120,'fairValue':1200}]
  self.assertIn('VAL_STALE_PRICE',self.codes(validate(self.s,2027)))
 def test_future_price(self):
  self.s['valuations']=[{'id':1,'assetId':1,'date':'2027-03-31','priceDate':'2027-04-01','qty':10,'price':120,'fairValue':1200}]
  self.assertIn('VAL_FUTURE_PRICE',self.codes(validate(self.s,2027)))
 def test_journal_imbalance(self):
  calc={'positions':[],'journals':[{'id':'j1','lines':[{'debit':100,'credit':0}]}],'stockPools':[]}
  self.assertIn('JRN_UNBALANCED',self.codes(validate(self.s,2027,calc)))
 def test_tax_pool_mismatch(self):
  calc={'positions':[{'assetId':1,'qty':10,'book':1000,'tax':900,'reserve':-100}], 'journals':[], 'stockPools':[{'stock_code':'005930','tax':1000}]}
  self.assertIn('REC_STOCK_TAX_POOL',self.codes(validate(self.s,2027,calc)))
 def test_missing_journals_not_passed(self):
  self.assertIn('JRN_UNVERIFIED',self.codes(validate(self.s,2027,{'positions':[]})))
if __name__=='__main__':unittest.main()
