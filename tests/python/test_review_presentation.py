"""Explanatory snapshots must not change calculation or historical approvals."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from app.server import storage,workpapers
from app.server.calculation import calculate_year
from app.database.bootstrap import create_database
from tests.python.test_tax_adjustment_rules import CASES,state_for


class ReviewPresentationTests(unittest.TestCase):
    def database(self,normalized):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        prior=storage.DB_PATH;storage.DB_PATH=Path(tmp.name)/'review.sqlite3'
        self.addCleanup(setattr,storage,'DB_PATH',prior)
        if normalized:create_database(storage.DB_PATH)

    def test_partial_disposal_explains_fixed_answers_in_both_storage_paths(self):
        for normalized in (False,True):
            with self.subTest(normalized=normalized):
                self.database(normalized);s=state_for(CASES[2]);s['roundingPolicy']={'profile':'KRW_WON_V1','effectiveYear':2026}
                before=calculate_year(s,2026);storage.save_state(s,0);workpapers.generate(2026,1)
                papers=workpapers.list_workpapers(2026)['items'];sale=next(w for w in papers if w['candidate']['event']=='SELL')
                d=sale['candidate']['review_detail']
                self.assertEqual((d['quantity_before'],d['quantity'],d['quantity_after']),(10,5,5))
                for key,value in dict(gross_amount='90',book_removed='70',tax_cost='50',reserve_before='-40',reserve_after='-20',disposed_prior_oci='20').items():self.assertEqual(d[key],value,key)
                self.assertEqual(calculate_year(s,2026),before,'explanations must not affect engine evidence')
                first=papers[0];rows=copy.deepcopy(first['candidate']['adjustment_plan']['rows']);rows[0]['label']='실제 승인한 조정 원인'
                workpapers.approve(first['id'],'검토자','조정 행과 원장 계산 과정을 확인했습니다.',rows,True)
                approved=workpapers.list_workpapers(2026)['items'][0]
                self.assertEqual(approved['approved']['adjustments'][0]['label'],'실제 승인한 조정 원인')
                frozen=copy.deepcopy(approved)
                s['assets'][0]['asset_name']='현재 바뀐 이름';s['valuations'][0]['price']=15;s['valuations'][0]['fairValue']=150
                storage.save_state(s,1)
                self.assertEqual(workpapers.list_workpapers(2026)['items'][0],frozen,'listing must not rebuild old explanations from live records')
                if normalized:
                    with storage.connect() as c:self.assertEqual(c.execute('SELECT label FROM tax_adjustment_rows ORDER BY adjustment_row_id LIMIT 1').fetchone()[0],'실제 승인한 조정 원인')

    def test_whole_won_residuals_are_used_in_explanation_not_unrounded_average(self):
        self.database(True)
        s=state_for(CASES[2]);s['roundingPolicy']={'profile':'KRW_WON_V1','effectiveYear':2026}
        s['trades'][0].update(qty=3,amount=100,price=100/3)
        s['trades'][1].update(qty=1,amount=40,price=40)
        s['valuations']=[]
        storage.save_state(s,0);workpapers.generate(2026,1)
        d=workpapers.list_workpapers(2026)['items'][0]['candidate']['review_detail']
        self.assertEqual(d['tax_cost'],'33');self.assertEqual(d['book_removed'],'33')
        self.assertEqual(d['book_after'],'67')

    def test_removed_and_previous_workpapers_are_not_backfilled(self):
        from unittest.mock import patch
        self.database(True);s=state_for(CASES[2]);storage.save_state(s,0)
        with patch('app.server.review_presentation.attach_review_details'):
            workpapers.generate(2026,1)
        wp=workpapers.list_workpapers(2026)['items'][0]
        self.assertNotIn('review_detail',wp['candidate'])
        workpapers.approve(wp['id'],'검토자','이전 회차의 승인 원문 보존 확인',wp['candidate']['adjustment_plan']['rows'],True)
        before=workpapers.list_workpapers(2026)['items'][0]
        workpapers.generate(2026,1)
        self.assertEqual(workpapers.list_workpapers(2026)['items'][0],before)

    def test_approval_label_boundaries_match_both_storage_paths(self):
        for normalized in (False,True):
            for label,expected in [('  ','담당자 검토 조정'),('  원인  ','원인'),('가'*200,'가'*200),('가'*201,None)]:
                with self.subTest(normalized=normalized,length=len(label)):
                    self.database(normalized);state=state_for(CASES[2]);storage.save_state(state,0);workpapers.generate(2026,1)
                    paper=workpapers.list_workpapers(2026)['items'][0]
                    rows=copy.deepcopy(paper['candidate']['adjustment_plan']['rows']);rows[0]['label']=label
                    if expected is None:
                        with self.assertRaisesRegex(ValueError,'200자'):
                            workpapers.approve(paper['id'],'검토자','양쪽 저장 경로의 조정 원인 검증',rows,True)
                        self.assertEqual(workpapers.list_workpapers(2026)['items'][0]['status'],'DRAFT')
                    else:
                        workpapers.approve(paper['id'],'검토자','양쪽 저장 경로의 조정 원인 검증',rows,True)
                        self.assertEqual(workpapers.list_workpapers(2026)['items'][0]['approved']['adjustments'][0]['label'],expected)
