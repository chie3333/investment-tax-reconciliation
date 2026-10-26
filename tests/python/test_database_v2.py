"""Normalized schema foundation checks; all writes use isolated temporary files."""
import hashlib
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database import APPLICATION_ID
from app.database import bootstrap as db

NOW='2026-10-26T00:00:00Z'


class DatabaseV2Tests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.directory=Path(temporary.name);self.path=self.directory/'new.sqlite3'
        db.create_database(self.path)
        self.c=db.connect_database(self.path);self.addCleanup(self.c.close)

    def insert(self,table,**values):
        columns=','.join(db.quote(k) for k in values)
        return self.c.execute(f'INSERT INTO {db.quote(table)} ({columns}) VALUES ({",".join("?" for _ in values)})',tuple(values.values())).lastrowid

    def company(self,identifier=1,year=2026):
        self.insert('companies',company_id=identifier,company_name='검토 회사',fiscal_end_month=12,created_at=NOW,updated_at=NOW)
        self.insert('fiscal_years',fiscal_year_id=identifier,company_id=identifier,fiscal_year=year,
                    start_date=f'{year}-01-01',end_date=f'{year}-12-31',created_at=NOW)

    def stock(self,identifier=1):
        return self.insert('stock_master',stock_id=identifier,stock_code=f'{identifier:06}',asset_name='검토 종목',market_type='KOSPI',created_at=NOW,updated_at=NOW)

    def source(self):
        self.company();self.stock()
        self.insert('investment_assets',asset_id=1,company_id=1,stock_id=1,classification='FVOCI',book_cost_method='MOVING',created_at=NOW,updated_at=NOW)
        self.insert('trade_events',trade_id=1,company_id=1,asset_id=1,fiscal_year_id=1,logical_key='trade:1',
                    trade_date='2026-01-02',event_type='BUY',quantity=10,execution_price='10',gross_amount='100',
                    version=1,is_current=1,status='ACTIVE',created_at=NOW)

    def calculation(self):
        return self.insert('calculation_runs',run_id=1,company_id=1,fiscal_year_id=1,as_of='2026-12-31',source_revision=0,
                    engine_version='test-only',rounding_profile_id='LEGACY_CENT_V1',inputs_json='{}',input_hash='test-input',
                    result_hash='test-result',status='SUCCEEDED',created_at=NOW)

    def paper(self,**changes):
        values=dict(workpaper_id=1,company_id=1,fiscal_year_id=1,run_id=1,evidence_state='VERIFIED',trade_id=1,
                    cycle=1,status='DRAFT',candidate_json='{}',checks_json='[]',rule_snapshot_json='{}',created_at=NOW)
        values.update(changes)
        return self.insert('tax_workpapers',**values)

    def adjustment(self,**changes):
        values=dict(adjustment_row_id=1,company_id=1,workpaper_id=1,asset_id=1,row_no=1,label='검토 행',
                    direction='익금산입',disposition='유보',amount='10',reserve_movement='10',legal_basis='테스트 산술 근거',created_at=NOW)
        values.update(changes)
        return self.insert('tax_adjustment_rows',**values)

    def decision(self,**changes):
        values=dict(decision_id=1,company_id=1,workpaper_id=1,action='APPROVE',actor='검토자',reason='테스트 전용 승인',
                    acknowledged=1,source_revision=0,proof_json='{}',approved_rows_json='[]',created_at=NOW)
        values.update(changes)
        return self.insert('workpaper_decisions',**values)

    def post(self,**changes):
        values=dict(entry_id=1,company_id=1,fiscal_year_id=1,asset_id=1,workpaper_id=1,decision_id=1,adjustment_row_id=1,
                    entry_type='POST',adjustment_key='FVOCI_TRADE',amount='10',memo='테스트 전기',recorded_at=NOW)
        values.update(changes)
        return self.insert('reserve_entries',**values)

    def legacy_close(self):
        return self.insert('fiscal_closes',close_id=1,company_id=1,fiscal_year_id=1,source_revision=0,evidence_state='LEGACY',
                    opening_json='{}',movements_json='{}',ending_json='{}',proof_json='{}',reviewer='이전 기록',note='테스트 원문',closed_at=NOW)

    def test_schema_matches_all_28_documented_tables_and_is_empty(self):
        definitions=(Path(__file__).resolve().parents[2]/'docs/DATABASE_TABLES.md').read_text()
        expected=set(re.findall(r'^### \d+\. `([a-z_]+)`',definitions,re.M))
        report=db.inspect_database(self.path)
        self.assertEqual(set(report['tables']),expected|{'schema_metadata'})
        self.assertEqual(len(expected),28);self.assertEqual(report['user_data_rows'],0)
        self.assertEqual(report['application_id'],APPLICATION_ID)
        self.assertEqual(report['integrity'],['ok']);self.assertEqual(report['foreign_key_errors'],[])
        self.assertTrue(report['object_hash_matches'])

    def test_every_foreign_key_has_a_nonpartial_unique_parent_key(self):
        tables=[r[0] for r in self.c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            groups={}
            for row in self.c.execute(f'PRAGMA foreign_key_list({db.quote(table)})'):
                groups.setdefault(row['id'],[]).append(row)
            for rows in groups.values():
                rows.sort(key=lambda r:r['seq']);parent=rows[0]['table']
                info=list(self.c.execute(f'PRAGMA table_info({db.quote(parent)})'))
                primary=tuple(r['name'] for r in sorted(info,key=lambda r:r['pk']) if r['pk'])
                columns=tuple(r['to'] for r in rows) if rows[0]['to'] is not None else primary
                keys=[primary]
                for index in self.c.execute(f'PRAGMA index_list({db.quote(parent)})'):
                    if index['unique'] and not index['partial']:
                        keys.append(tuple(r['name'] for r in self.c.execute(f'PRAGMA index_info({db.quote(index["name"])})')))
                self.assertIn(columns,keys,(table,parent,columns))

    def test_no_existing_file_is_overwritten_even_if_empty(self):
        for name,content in (('empty.sqlite3',b''),('existing.sqlite3',b'not to overwrite')):
            path=self.directory/name;path.write_bytes(content)
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaises(FileExistsError):db.create_database(path)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
        with self.assertRaises(FileExistsError):db.create_database(self.path)

    def test_failed_creation_does_not_publish_a_partial_database(self):
        path=self.directory/'failed.sqlite3'
        with patch.object(db,'add_triggers',side_effect=RuntimeError('test failure')):
            with self.assertRaises(RuntimeError):db.create_database(path)
        self.assertFalse(path.exists());self.assertEqual(list(self.directory.glob('.investment-v2-*')),[])

    def test_read_only_inspection_does_not_create_or_modify_files(self):
        absent=self.directory/'absent.sqlite3'
        self.assertFalse(db.inspect_database(absent)['exists']);self.assertFalse(absent.exists())
        with self.assertRaises(sqlite3.OperationalError):db.connect_database(absent)
        before=self.path.read_bytes();db.inspect_database(self.path);self.assertEqual(self.path.read_bytes(),before)
        c=db.connect_database(self.path,read_only=True)
        try:
            with self.assertRaises(sqlite3.OperationalError):c.execute('DELETE FROM companies')
        finally:c.close()

    def test_application_connects_without_creating_legacy_tables(self):
        from app.server import storage
        self.c.commit()
        with patch.object(storage,'DB_PATH',self.path):
            with storage.connect() as c:
                self.assertEqual(storage.read_state(c)['revision'],0)
        self.assertFalse(self.c.execute("SELECT 1 FROM sqlite_master WHERE name='workspace'").fetchone())

    def test_company_and_year_foreign_keys_reject_cross_links(self):
        self.source();self.company(2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert('trade_events',company_id=2,asset_id=1,fiscal_year_id=2,logical_key='cross',trade_date='2026-02-01',
                        event_type='BUY',quantity=1,execution_price='1',gross_amount='1',version=1,is_current=1,status='ACTIVE',created_at=NOW)
        self.calculation()
        with self.assertRaises(sqlite3.IntegrityError):self.paper(company_id=2,fiscal_year_id=2)

    def test_dates_quantities_and_period_overlap(self):
        self.source()
        for start,end in (('2026-02-30','2027-01-01'),('2026-12-01','2027-03-01')):
            with self.assertRaises(sqlite3.IntegrityError):
                self.insert('fiscal_years',company_id=1,fiscal_year=2027,start_date=start,end_date=end,created_at=NOW)
        for qty,day in ((0,'2026-02-01'),(1.5,'2026-02-01'),(1,'2027-01-01')):
            with self.assertRaises(sqlite3.IntegrityError):
                self.insert('trade_events',company_id=1,asset_id=1,fiscal_year_id=1,logical_key='invalid',trade_date=day,
                            event_type='BUY',quantity=qty,execution_price='1',gross_amount='1',version=1,is_current=1,status='ACTIVE',created_at=NOW)

    def test_prices_are_exact_versioned_and_immutable(self):
        self.stock()
        values=dict(stock_id=1,price_date='2026-06-30',provider='TEST',quote_revision=1,currency='KRW',raw_row_json='{}',fetched_at=NOW)
        for value in ('NaN','Infinity','1e2','1.2.3','-1','-0','01.2','', '1'*129):
            with self.assertRaises(sqlite3.IntegrityError):self.insert('market_prices',**values,close_price=value,payload_hash=value)
        original=self.insert('market_prices',**values,close_price='9007199254740993.01',payload_hash='a')
        self.assertEqual(self.c.execute('SELECT close_price FROM market_prices').fetchone()[0],'9007199254740993.01')
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("UPDATE market_prices SET close_price='2'")
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute('DELETE FROM market_prices')
        values.update(quote_revision=2,supersedes_price_id=original)
        self.insert('market_prices',**values,close_price='9007199254740993.02',payload_hash='b')
        with self.assertRaises(sqlite3.IntegrityError):self.insert('market_prices',**values,close_price='3',payload_hash='c')

    def test_wrong_quote_stock_and_current_source_duplicates(self):
        self.source();self.stock(2)
        price=self.insert('market_prices',stock_id=2,price_date='2026-06-30',provider='TEST',quote_revision=1,
                          close_price='14',currency='KRW',payload_hash='x',raw_row_json='{}',fetched_at=NOW)
        with self.assertRaisesRegex(sqlite3.IntegrityError,'another stock'):
            self.insert('valuation_events',company_id=1,asset_id=1,fiscal_year_id=1,logical_key='v:1',valuation_date='2026-06-30',
                        quantity=10,price_id=price,applied_price='14',fair_value='140',price_date='2026-06-30',source='TEST',version=1,is_current=1,status='ACTIVE',created_at=NOW)
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("UPDATE trade_events SET gross_amount='999'")
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute('DELETE FROM trade_events')
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("UPDATE investment_assets SET classification='FVTPL'")

    def test_workpaper_xor_and_null_aware_uniqueness(self):
        self.source();self.calculation()
        with self.assertRaises(sqlite3.IntegrityError):self.paper(trade_id=None)
        self.paper(run_id=None,evidence_state='LEGACY')
        with self.assertRaises(sqlite3.IntegrityError):self.paper(workpaper_id=2,run_id=None,evidence_state='LEGACY')
        self.paper(workpaper_id=3,run_id=None,evidence_state='LEGACY',cycle=2)

    def test_approval_post_cancel_reversal_and_immutable_rows(self):
        self.source();self.calculation();self.paper()
        for values in (dict(direction='손금산입'),dict(disposition='기타'),dict(amount='0')):
            with self.assertRaises(sqlite3.IntegrityError):self.adjustment(**values)
        self.adjustment();self.decision();self.post()
        with self.assertRaises(sqlite3.IntegrityError):self.decision(decision_id=2)
        with self.assertRaises(sqlite3.IntegrityError):self.post(entry_id=2)
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("UPDATE tax_adjustment_rows SET label='changed'")
        self.decision(decision_id=2,action='CANCEL',cancel_of_decision_id=1)
        self.post(entry_id=2,decision_id=2,entry_type='REVERSAL',reversal_of_entry_id=1,amount='-10')
        with self.assertRaises(sqlite3.IntegrityError):self.post(entry_id=3,decision_id=2,entry_type='REVERSAL',reversal_of_entry_id=1,amount='-10')
        self.assertEqual([r[0] for r in self.c.execute('SELECT amount FROM reserve_entries ORDER BY entry_id')],['10','-10'])
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute('DELETE FROM reserve_entries')

    def test_legacy_and_stale_evidence_cannot_approve(self):
        self.source();self.calculation();self.paper(run_id=None,evidence_state='LEGACY')
        with self.assertRaises(sqlite3.IntegrityError):self.decision()
        self.paper(workpaper_id=2,cycle=2)
        self.c.execute('UPDATE companies SET source_revision=1')
        with self.assertRaises(sqlite3.IntegrityError):self.decision(workpaper_id=2)

    def test_closed_period_and_policy_backdating_are_blocked(self):
        self.source();self.legacy_close()
        self.assertEqual(self.c.execute('SELECT status FROM fiscal_years').fetchone()[0],'CLOSED')
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute('UPDATE trade_events SET is_current=0')
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("UPDATE fiscal_years SET status='OPEN'")
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute('UPDATE companies SET fiscal_end_month=3')
        values=dict(company_id=1,is_legacy_baseline=0,disposal='RETAIN',confirmation_status='CONFIRMED',reviewer='검토자',reason='연도별 적용',version=1,is_current=1,created_at=NOW)
        with self.assertRaises(sqlite3.IntegrityError):self.insert('fvoci_policy_versions',effective_start_year=2026,**values)
        self.insert('fvoci_policy_versions',effective_start_year=2027,**values)
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("UPDATE fvoci_policy_versions SET disposal='TRANSFER'")

    def test_balance_equations_preserve_fractional_amounts(self):
        self.source();self.calculation()
        values=dict(company_id=1,run_id=1,asset_id=1,quantity=10,book_value='140.01',tax_value='100.00',reserve='-40.01',
                    oci='40.01',active_oci='40.01',retained_disposal_oci='0',profit_loss='0',dividend='0')
        with self.assertRaises(sqlite3.IntegrityError):self.insert('calculated_balances',**{**values,'reserve':'-40'})
        self.insert('calculated_balances',**values)
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("UPDATE calculated_balances SET book_value='999'")

    def test_schema_object_tampering_is_reported(self):
        self.c.execute('CREATE TABLE unexpected(value TEXT)');self.c.commit()
        self.assertFalse(db.inspect_database(self.path)['object_hash_matches'])
