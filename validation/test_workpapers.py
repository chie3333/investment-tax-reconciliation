import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class WorkpaperTests(unittest.TestCase):
    def test_generate_approve_post_once_and_lock(self):
        with tempfile.TemporaryDirectory() as td:
            import storage
            old=storage.DB_PATH;storage.DB_PATH=Path(td)/'test.sqlite3'
            try:
                from workpapers import generate,list_workpapers,approve
                state={'master':[],'assets':[{'id':1,'stock_code':'005930','classification':'FVOCI'}],
                       'trades':[],'valuations':[{'id':3,'assetId':1,'date':'2026-12-31'}],
                       'adjustments':[],'taxPolicyYears':[],'taxPolicy':{},'taxBasis':{},'stockPolicies':{},'fiscalEndMonth':12,'ids':{}}
                storage.save_state(state,0)
                item={'asset_id':1,'source_type':'valuation','source_id':3,'classification':'FVOCI','candidate_adjustment':0,'reserve_change':-200,'book_pl':0,'tax_pl':0,'oci_change':200,'oci_equity_transfer':0}
                self.assertEqual(generate(2026,1,[item])['created'],1)
                self.assertEqual(generate(2026,1,[item])['created'],0)
                wp=list_workpapers(2026)['items'][0]
                self.assertEqual(wp['status'],'DRAFT')
                adj=[{'direction':'손금산입','disposition':'△유보','amount':'200','reserve_movement':'-200','legal_basis':'검토된 적용 규정'}]
                with self.assertRaises(ValueError):approve(wp['id'],'검토자','검토 근거를 충분히 기재',adj,False)
                self.assertEqual(approve(wp['id'],'검토자','검토 근거를 충분히 기재',adj,True)['reserve_posted'],1)
                from closing import summary
                self.assertEqual(summary(2026)['movements']['1|FVOCI_VALUATION'],'-200')
                with self.assertRaises(ValueError):approve(wp['id'],'검토자','검토 근거를 충분히 기재',adj,True)
            finally:storage.DB_PATH=old

    def test_reject_invalid_source(self):
        with tempfile.TemporaryDirectory() as td:
            import storage
            old=storage.DB_PATH;storage.DB_PATH=Path(td)/'test.sqlite3'
            try:
                from workpapers import generate
                state={'master':[],'assets':[],'trades':[],'valuations':[],'adjustments':[],'taxPolicyYears':[]}
                storage.save_state(state,0)
                with self.assertRaises(ValueError):generate(2026,1,[{'asset_id':1,'source_type':'trade','source_id':9}])
            finally:storage.DB_PATH=old
