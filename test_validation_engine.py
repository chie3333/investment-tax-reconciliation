import unittest
from validation_engine import validate

class ValidationTests(unittest.TestCase):
 def state(self):
  return {'fiscalEndMonth':3,'assets':[{'id':1,'stock_code':'005930','classification':'FVTPL'}], 'trades':[{'id':1,'assetId':1,'date':'2026-04-01','type':'BUY','qty':10,'price':100,'amount':1000}], 'valuations':[], 'adjustments':[], 'taxPolicyYears':[{'effectiveYear':'2027','method':'TOTAL','filingStatus':'NOT_FILED','reviewStatus':'CONFIRMED','changeStatus':'NO'}]}
 def codes(self, result):return {x['code'] for x in result['issues']}
 def test_missing_closing_valuation(self):
  self.assertIn('VAL_MISSING',self.codes(validate(self.state(),2027)))
 def test_over_sale_and_quantity_reconciliation(self):
  s=self.state();s['trades'].append({'id':2,'assetId':1,'date':'2026-05-01','type':'SELL','qty':11,'price':100,'amount':1100})
  codes=self.codes(validate(s,2027,{'positions':[{'assetId':1,'qty':10,'book':1000,'tax':1000,'reserve':0}]}))
  self.assertIn('QTY_OVERSELL',codes);self.assertIn('REC_QTY',codes)
 def test_policy_change(self):
  s=self.state();s['taxPolicyYears'].append({'effectiveYear':'2028','method':'MOVING','filingStatus':'FILED','reviewStatus':'PENDING','changeStatus':'PENDING'})
  self.assertIn('POL_CHANGE_INCONSISTENT',self.codes(validate(s,2028)))
 def test_book_tax_reconcile(self):
  codes=self.codes(validate(self.state(),2027,{'positions':[{'assetId':1,'qty':10,'book':1200,'tax':1000,'reserve':0}]}))
  self.assertIn('REC_RESERVE',codes)
 def test_carryforward_not_misrepresented(self):
  self.assertIn('RES_CARRYFORWARD_UNVERIFIED',self.codes(validate(self.state(),2027,{'positions':[]})))
if __name__=='__main__':unittest.main()
