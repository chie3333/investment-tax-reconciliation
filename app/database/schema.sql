-- Normalized application storage, version 2. Use app.database connections:
-- decimal_valid/sign/equal/difference functions are exact Decimal validators.
-- Existing legacy databases remain on their original storage path.

CREATE TABLE schema_metadata (
 id INTEGER PRIMARY KEY CHECK(id=1), schema_version INTEGER NOT NULL,
 ddl_hash TEXT NOT NULL, object_hash TEXT NOT NULL, created_at TEXT NOT NULL
);

CREATE TABLE companies (
 company_id INTEGER PRIMARY KEY, company_name TEXT NOT NULL CHECK(length(trim(company_name))>0),
 business_number TEXT, fiscal_end_month INTEGER NOT NULL CHECK(fiscal_end_month BETWEEN 1 AND 12),
 source_revision INTEGER NOT NULL DEFAULT 0 CHECK(source_revision>=0),
 status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','ARCHIVED')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 settings_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(settings_json))
);
CREATE TABLE fiscal_years (
 fiscal_year_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies ON DELETE RESTRICT,
 fiscal_year INTEGER NOT NULL CHECK(fiscal_year BETWEEN 1900 AND 2200),
 start_date TEXT NOT NULL CHECK(date_valid(start_date)), end_date TEXT NOT NULL CHECK(date_valid(end_date)),
 status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','CLOSED')), created_at TEXT NOT NULL,
 CHECK(start_date<=end_date), UNIQUE(company_id,fiscal_year), UNIQUE(company_id,fiscal_year_id)
);
CREATE TABLE stock_master (
 stock_id INTEGER PRIMARY KEY, stock_code TEXT NOT NULL CHECK(length(trim(stock_code))>0),
 asset_name TEXT NOT NULL CHECK(length(trim(asset_name))>0), market_type TEXT NOT NULL,
 isin TEXT UNIQUE, instrument_type TEXT, issuer_country TEXT, currency TEXT,
 listing_status TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK(listing_status IN ('LISTED','DELISTED','UNKNOWN')),
 is_current_code INTEGER NOT NULL DEFAULT 1 CHECK(is_current_code IN (0,1)), source TEXT, fetched_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX stock_current_code ON stock_master(market_type,stock_code) WHERE is_current_code=1;
CREATE INDEX stock_name ON stock_master(asset_name);
CREATE TABLE market_prices (
 price_id INTEGER PRIMARY KEY, stock_id INTEGER NOT NULL REFERENCES stock_master ON DELETE RESTRICT,
 price_date TEXT NOT NULL CHECK(date_valid(price_date)), provider TEXT NOT NULL,
 quote_revision INTEGER NOT NULL CHECK(quote_revision>=1),
 close_price TEXT NOT NULL CHECK(decimal_valid(close_price) AND decimal_sign(close_price)>=0),
 currency TEXT NOT NULL, payload_hash TEXT NOT NULL, raw_row_json TEXT NOT NULL CHECK(json_valid(raw_row_json)),
 fetched_at TEXT NOT NULL, supersedes_price_id INTEGER,
 UNIQUE(stock_id,price_date,provider,quote_revision), UNIQUE(stock_id,price_date,provider,payload_hash),
 UNIQUE(stock_id,price_date,provider,price_id),
 FOREIGN KEY(stock_id,price_date,provider,supersedes_price_id)
 REFERENCES market_prices(stock_id,price_date,provider,price_id) ON DELETE RESTRICT,
 CHECK(supersedes_price_id IS NULL OR supersedes_price_id<>price_id)
);
CREATE TABLE investment_assets (
 asset_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies ON DELETE RESTRICT,
 stock_id INTEGER NOT NULL REFERENCES stock_master ON DELETE RESTRICT,
 classification TEXT NOT NULL CHECK(classification IN ('FVOCI','FVTPL')),
 book_cost_method TEXT NOT NULL CHECK(book_cost_method IN ('TOTAL','MOVING','FIFO','SPECIFIC')),
 status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','ARCHIVED')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json)),
 UNIQUE(company_id,stock_id,classification), UNIQUE(company_id,asset_id)
);
CREATE TABLE trade_events (
 trade_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, asset_id INTEGER NOT NULL, fiscal_year_id INTEGER NOT NULL,
 logical_key TEXT NOT NULL CHECK(length(trim(logical_key))>0), trade_date TEXT NOT NULL CHECK(date_valid(trade_date)),
 event_type TEXT NOT NULL CHECK(event_type IN ('BUY','SELL','DIVIDEND')),
 quantity INTEGER CHECK(quantity IS NULL OR (typeof(quantity)='integer' AND quantity>0)),
 execution_price TEXT CHECK(execution_price IS NULL OR (decimal_valid(execution_price) AND decimal_sign(execution_price)>=0)),
 gross_amount TEXT NOT NULL CHECK(decimal_valid(gross_amount) AND decimal_sign(gross_amount)>=0),
 fees TEXT CHECK(fees IS NULL OR (decimal_valid(fees) AND decimal_sign(fees)>=0)),
 transaction_tax TEXT CHECK(transaction_tax IS NULL OR (decimal_valid(transaction_tax) AND decimal_sign(transaction_tax)>=0)),
 net_cash_amount TEXT CHECK(net_cash_amount IS NULL OR decimal_valid(net_cash_amount)),
 note TEXT, evidence_json TEXT CHECK(evidence_json IS NULL OR json_valid(evidence_json)),
 version INTEGER NOT NULL CHECK(version>=1), is_current INTEGER NOT NULL CHECK(is_current IN (0,1)),
 supersedes_id INTEGER, status TEXT NOT NULL CHECK(status IN ('ACTIVE','VOID')), created_at TEXT NOT NULL,
 CHECK(event_type='DIVIDEND' OR (quantity IS NOT NULL AND execution_price IS NOT NULL)),
 UNIQUE(company_id,logical_key,version), UNIQUE(company_id,trade_id),
 UNIQUE(company_id,fiscal_year_id,trade_id), UNIQUE(company_id,logical_key,trade_id),
 FOREIGN KEY(company_id,asset_id) REFERENCES investment_assets(company_id,asset_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,fiscal_year_id) REFERENCES fiscal_years(company_id,fiscal_year_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,logical_key,supersedes_id) REFERENCES trade_events(company_id,logical_key,trade_id) ON DELETE RESTRICT,
 CHECK(supersedes_id IS NULL OR supersedes_id<>trade_id)
);
CREATE UNIQUE INDEX trade_current ON trade_events(company_id,logical_key) WHERE is_current=1;
CREATE INDEX trade_order ON trade_events(company_id,asset_id,trade_date,trade_id);
CREATE TABLE valuation_events (
 valuation_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, asset_id INTEGER NOT NULL, fiscal_year_id INTEGER NOT NULL,
 logical_key TEXT NOT NULL, valuation_date TEXT NOT NULL CHECK(date_valid(valuation_date)),
 quantity INTEGER NOT NULL CHECK(typeof(quantity)='integer' AND quantity>0),
 price_id INTEGER REFERENCES market_prices ON DELETE RESTRICT,
 applied_price TEXT NOT NULL CHECK(decimal_valid(applied_price) AND decimal_sign(applied_price)>=0),
 fair_value TEXT NOT NULL CHECK(decimal_valid(fair_value) AND decimal_sign(fair_value)>=0),
 price_date TEXT NOT NULL CHECK(date_valid(price_date)), source TEXT NOT NULL, note TEXT,
 evidence_json TEXT CHECK(evidence_json IS NULL OR json_valid(evidence_json)),
 version INTEGER NOT NULL CHECK(version>=1), is_current INTEGER NOT NULL CHECK(is_current IN (0,1)),
 supersedes_id INTEGER, status TEXT NOT NULL CHECK(status IN ('ACTIVE','VOID')), created_at TEXT NOT NULL,
 UNIQUE(company_id,logical_key,version), UNIQUE(company_id,valuation_id),
 UNIQUE(company_id,fiscal_year_id,valuation_id), UNIQUE(company_id,logical_key,valuation_id),
 FOREIGN KEY(company_id,asset_id) REFERENCES investment_assets(company_id,asset_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,fiscal_year_id) REFERENCES fiscal_years(company_id,fiscal_year_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,logical_key,supersedes_id) REFERENCES valuation_events(company_id,logical_key,valuation_id) ON DELETE RESTRICT,
 CHECK(supersedes_id IS NULL OR supersedes_id<>valuation_id)
);
CREATE UNIQUE INDEX valuation_current ON valuation_events(company_id,logical_key) WHERE is_current=1;
CREATE UNIQUE INDEX valuation_active_date ON valuation_events(company_id,asset_id,valuation_date) WHERE is_current=1 AND status='ACTIVE';
CREATE INDEX valuation_order ON valuation_events(company_id,asset_id,valuation_date,valuation_id);

CREATE TABLE tax_cost_policy_versions (
 policy_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies ON DELETE RESTRICT,
 effective_start_year INTEGER CHECK(effective_start_year BETWEEN 1900 AND 2200),
 is_legacy_baseline INTEGER NOT NULL CHECK(is_legacy_baseline IN (0,1)),
 method TEXT NOT NULL CHECK(method IN ('TOTAL','MOVING')),
 filing_status TEXT NOT NULL CHECK(filing_status IN ('UNKNOWN','FILED','NOT_FILED')),
 filing_date TEXT CHECK(filing_date IS NULL OR date_valid(filing_date)), receipt TEXT,
 change_status TEXT NOT NULL CHECK(change_status IN ('NO','YES','UNKNOWN')), previous_method TEXT CHECK(previous_method IN ('TOTAL','MOVING')),
 reviewer TEXT, reason TEXT, evidence_json TEXT CHECK(evidence_json IS NULL OR json_valid(evidence_json)),
 version INTEGER NOT NULL CHECK(version>=1), is_current INTEGER NOT NULL CHECK(is_current IN (0,1)),
 supersedes_id INTEGER, created_at TEXT NOT NULL,
 CHECK((is_legacy_baseline=1 AND effective_start_year IS NULL) OR
       (is_legacy_baseline=0 AND effective_start_year IS NOT NULL AND length(trim(reviewer))>0 AND length(trim(reason))>0 AND reviewer IS NOT NULL AND reason IS NOT NULL)),
 UNIQUE(company_id,policy_id), FOREIGN KEY(company_id,supersedes_id) REFERENCES tax_cost_policy_versions(company_id,policy_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX tax_policy_year ON tax_cost_policy_versions(company_id,effective_start_year) WHERE is_current=1 AND is_legacy_baseline=0;
CREATE UNIQUE INDEX tax_policy_baseline ON tax_cost_policy_versions(company_id) WHERE is_current=1 AND is_legacy_baseline=1;
CREATE UNIQUE INDEX tax_policy_version ON tax_cost_policy_versions(company_id,ifnull(effective_start_year,0),version);
CREATE TABLE fvoci_policy_versions (
 policy_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies ON DELETE RESTRICT,
 effective_start_year INTEGER CHECK(effective_start_year BETWEEN 1900 AND 2200),
 is_legacy_baseline INTEGER NOT NULL CHECK(is_legacy_baseline IN (0,1)),
 disposal TEXT NOT NULL CHECK(disposal IN ('TRANSFER','RETAIN')),
 confirmation_status TEXT NOT NULL CHECK(confirmation_status IN ('CONFIRMED','UNKNOWN')),
 reviewer TEXT, reason TEXT, version INTEGER NOT NULL CHECK(version>=1), is_current INTEGER NOT NULL CHECK(is_current IN (0,1)),
 supersedes_id INTEGER, created_at TEXT NOT NULL,
 metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json)),
 CHECK((is_legacy_baseline=1 AND effective_start_year IS NULL) OR
       (is_legacy_baseline=0 AND effective_start_year IS NOT NULL AND confirmation_status='CONFIRMED' AND reviewer IS NOT NULL AND reason IS NOT NULL AND length(trim(reviewer))>0 AND length(trim(reason))>0)),
 UNIQUE(company_id,policy_id), FOREIGN KEY(company_id,supersedes_id) REFERENCES fvoci_policy_versions(company_id,policy_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX fvoci_policy_year ON fvoci_policy_versions(company_id,effective_start_year) WHERE is_current=1 AND is_legacy_baseline=0;
CREATE UNIQUE INDEX fvoci_policy_baseline ON fvoci_policy_versions(company_id) WHERE is_current=1 AND is_legacy_baseline=1;
CREATE UNIQUE INDEX fvoci_policy_version ON fvoci_policy_versions(company_id,ifnull(effective_start_year,0),version);
CREATE TABLE company_rule_reviews (
 review_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, fiscal_year_id INTEGER NOT NULL,
 rule_version TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), confirmed INTEGER NOT NULL CHECK(confirmed IN (0,1)),
 reviewer TEXT, reason TEXT, exclusions_json TEXT NOT NULL CHECK(json_valid(exclusions_json)),
 legal_sources_json TEXT NOT NULL CHECK(json_valid(legal_sources_json)), checked_on TEXT NOT NULL CHECK(date_valid(checked_on)),
 version INTEGER NOT NULL CHECK(version>=1), is_current INTEGER NOT NULL CHECK(is_current IN (0,1)), supersedes_id INTEGER, created_at TEXT NOT NULL,
 metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json)),
 CHECK(enabled=0 OR (confirmed=1 AND reviewer IS NOT NULL AND reason IS NOT NULL AND length(trim(reviewer))>0 AND length(trim(reason))>0)),
 UNIQUE(company_id,fiscal_year_id,version), UNIQUE(company_id,fiscal_year_id,review_id),
 FOREIGN KEY(company_id,fiscal_year_id) REFERENCES fiscal_years(company_id,fiscal_year_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,fiscal_year_id,supersedes_id) REFERENCES company_rule_reviews(company_id,fiscal_year_id,review_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX rule_review_current ON company_rule_reviews(company_id,fiscal_year_id) WHERE is_current=1;
CREATE TABLE rounding_profiles (
 rounding_profile_id TEXT PRIMARY KEY NOT NULL, description TEXT NOT NULL,
 parameters_json TEXT NOT NULL CHECK(json_valid(parameters_json)), status TEXT NOT NULL CHECK(status IN ('LEGACY','DRAFT','APPROVED')),
 reviewer TEXT, reason TEXT, created_at TEXT NOT NULL,
 CHECK(status<>'APPROVED' OR (reviewer IS NOT NULL AND reason IS NOT NULL AND length(trim(reviewer))>0 AND length(trim(reason))>0))
);

CREATE TABLE calculation_runs (
 run_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, fiscal_year_id INTEGER NOT NULL,
 as_of TEXT NOT NULL CHECK(date_valid(as_of)), source_revision INTEGER NOT NULL CHECK(source_revision>=0), engine_version TEXT NOT NULL,
 rounding_profile_id TEXT NOT NULL REFERENCES rounding_profiles ON DELETE RESTRICT,
 inputs_json TEXT NOT NULL CHECK(json_valid(inputs_json)), input_hash TEXT NOT NULL, result_hash TEXT,
 status TEXT NOT NULL CHECK(status IN ('SUCCEEDED','FAILED')), error_json TEXT CHECK(error_json IS NULL OR json_valid(error_json)), created_at TEXT NOT NULL,
 CHECK(status<>'SUCCEEDED' OR (result_hash IS NOT NULL AND length(result_hash)>0)),
 UNIQUE(company_id,run_id), UNIQUE(company_id,fiscal_year_id,run_id),
 FOREIGN KEY(company_id,fiscal_year_id) REFERENCES fiscal_years(company_id,fiscal_year_id) ON DELETE RESTRICT
);
CREATE INDEX calculation_lookup ON calculation_runs(company_id,fiscal_year_id,source_revision,created_at);
CREATE TABLE calculated_balances (
 balance_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, run_id INTEGER NOT NULL, asset_id INTEGER NOT NULL,
 quantity INTEGER NOT NULL CHECK(typeof(quantity)='integer' AND quantity>=0),
 book_value TEXT NOT NULL CHECK(decimal_valid(book_value)), tax_value TEXT NOT NULL CHECK(decimal_valid(tax_value)),
 reserve TEXT NOT NULL CHECK(decimal_valid(reserve)), oci TEXT NOT NULL CHECK(decimal_valid(oci)),
 active_oci TEXT NOT NULL CHECK(decimal_valid(active_oci)), retained_disposal_oci TEXT NOT NULL CHECK(decimal_valid(retained_disposal_oci)),
 profit_loss TEXT NOT NULL CHECK(decimal_valid(profit_loss)), dividend TEXT NOT NULL CHECK(decimal_valid(dividend)),
 CHECK(decimal_difference(tax_value,book_value,reserve)), CHECK(decimal_difference(oci,active_oci,retained_disposal_oci)),
 CHECK(quantity<>0 OR (decimal_sign(book_value)=0 AND decimal_sign(tax_value)=0)), UNIQUE(run_id,asset_id),
 FOREIGN KEY(company_id,run_id) REFERENCES calculation_runs(company_id,run_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,asset_id) REFERENCES investment_assets(company_id,asset_id) ON DELETE RESTRICT
);
CREATE TABLE calculated_stock_pools (
 pool_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, run_id INTEGER NOT NULL,
 stock_id INTEGER NOT NULL REFERENCES stock_master ON DELETE RESTRICT, quantity INTEGER NOT NULL CHECK(quantity>=0),
 tax_value TEXT NOT NULL CHECK(decimal_valid(tax_value)), method_snapshot TEXT NOT NULL CHECK(json_valid(method_snapshot)),
 UNIQUE(run_id,stock_id), FOREIGN KEY(company_id,run_id) REFERENCES calculation_runs(company_id,run_id) ON DELETE RESTRICT
);
CREATE TABLE validation_runs (
 validation_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, run_id INTEGER, fiscal_year_id INTEGER NOT NULL,
 source_revision INTEGER NOT NULL CHECK(source_revision>=0), evidence_state TEXT NOT NULL CHECK(evidence_state IN ('VERIFIED','LEGACY')),
 summary_json TEXT NOT NULL CHECK(json_valid(summary_json)), issues_json TEXT NOT NULL CHECK(json_valid(issues_json)), created_at TEXT NOT NULL,
 result_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(result_json)),
 CHECK(evidence_state='LEGACY' OR run_id IS NOT NULL), UNIQUE(company_id,fiscal_year_id,validation_id),
 UNIQUE(company_id,fiscal_year_id,run_id,validation_id),
 FOREIGN KEY(company_id,fiscal_year_id) REFERENCES fiscal_years(company_id,fiscal_year_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,fiscal_year_id,run_id) REFERENCES calculation_runs(company_id,fiscal_year_id,run_id) ON DELETE RESTRICT
);
CREATE TABLE issue_reviews (
 issue_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, fiscal_year_id INTEGER NOT NULL,
 issue_code TEXT NOT NULL, source_ref TEXT NOT NULL, validation_id INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('OPEN','REVIEWING','RESOLVED','ACCEPTED')), reason TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(company_id,fiscal_year_id,issue_code,source_ref), UNIQUE(company_id,fiscal_year_id,issue_id),
 FOREIGN KEY(company_id,fiscal_year_id,validation_id) REFERENCES validation_runs(company_id,fiscal_year_id,validation_id) ON DELETE RESTRICT
);
CREATE TABLE issue_review_events (
 event_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, fiscal_year_id INTEGER NOT NULL,
 issue_id INTEGER NOT NULL, validation_id INTEGER NOT NULL,
 previous_status TEXT CHECK(previous_status IN ('OPEN','REVIEWING','RESOLVED','ACCEPTED')),
 status TEXT NOT NULL CHECK(status IN ('OPEN','REVIEWING','RESOLVED','ACCEPTED')), actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(company_id,fiscal_year_id,issue_id) REFERENCES issue_reviews(company_id,fiscal_year_id,issue_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,fiscal_year_id,validation_id) REFERENCES validation_runs(company_id,fiscal_year_id,validation_id) ON DELETE RESTRICT
);

CREATE TABLE tax_workpapers (
 workpaper_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, fiscal_year_id INTEGER NOT NULL, run_id INTEGER,
 evidence_state TEXT NOT NULL CHECK(evidence_state IN ('VERIFIED','LEGACY')), trade_id INTEGER, valuation_id INTEGER, rule_review_id INTEGER,
 cycle INTEGER NOT NULL CHECK(cycle>=1), status TEXT NOT NULL CHECK(status IN ('DRAFT','APPROVED','CANCELLED','SUPERSEDED')),
 candidate_json TEXT NOT NULL CHECK(json_valid(candidate_json)), checks_json TEXT NOT NULL CHECK(json_valid(checks_json)),
 rule_snapshot_json TEXT NOT NULL CHECK(json_valid(rule_snapshot_json)), created_at TEXT NOT NULL,
 CHECK((trade_id IS NOT NULL)+(valuation_id IS NOT NULL)=1), CHECK(evidence_state='LEGACY' OR run_id IS NOT NULL),
 UNIQUE(company_id,workpaper_id), UNIQUE(company_id,fiscal_year_id,workpaper_id),
 FOREIGN KEY(company_id,fiscal_year_id) REFERENCES fiscal_years(company_id,fiscal_year_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,fiscal_year_id,run_id) REFERENCES calculation_runs(company_id,fiscal_year_id,run_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,fiscal_year_id,trade_id) REFERENCES trade_events(company_id,fiscal_year_id,trade_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,fiscal_year_id,valuation_id) REFERENCES valuation_events(company_id,fiscal_year_id,valuation_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,fiscal_year_id,rule_review_id) REFERENCES company_rule_reviews(company_id,fiscal_year_id,review_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX workpaper_trade ON tax_workpapers(company_id,fiscal_year_id,run_id,trade_id,cycle) WHERE trade_id IS NOT NULL AND run_id IS NOT NULL;
CREATE UNIQUE INDEX workpaper_valuation ON tax_workpapers(company_id,fiscal_year_id,run_id,valuation_id,cycle) WHERE valuation_id IS NOT NULL AND run_id IS NOT NULL;
CREATE UNIQUE INDEX workpaper_legacy_trade ON tax_workpapers(company_id,fiscal_year_id,trade_id,cycle) WHERE trade_id IS NOT NULL AND run_id IS NULL;
CREATE UNIQUE INDEX workpaper_legacy_valuation ON tax_workpapers(company_id,fiscal_year_id,valuation_id,cycle) WHERE valuation_id IS NOT NULL AND run_id IS NULL;
CREATE INDEX workpaper_status ON tax_workpapers(company_id,fiscal_year_id,status);
CREATE TABLE tax_adjustment_rows (
 adjustment_row_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, workpaper_id INTEGER NOT NULL, asset_id INTEGER NOT NULL,
 row_no INTEGER NOT NULL CHECK(row_no>=1), rule_id TEXT, label TEXT NOT NULL,
 direction TEXT NOT NULL CHECK(direction IN ('익금산입','익금불산입','손금산입','손금불산입')),
 disposition TEXT NOT NULL CHECK(disposition IN ('유보','△유보','기타','기타사외유출','상여','배당')),
 amount TEXT NOT NULL CHECK(decimal_valid(amount) AND decimal_sign(amount)>0),
 reserve_movement TEXT NOT NULL CHECK(decimal_valid(reserve_movement)),
 legal_basis TEXT NOT NULL CHECK(length(trim(legal_basis))>0), created_at TEXT NOT NULL,
 CHECK((disposition='유보' AND decimal_sign(reserve_movement)=1 AND decimal_equal(amount,reserve_movement) AND direction IN ('익금산입','손금불산입')) OR
       (disposition='△유보' AND decimal_sign(reserve_movement)=-1 AND decimal_difference('0',reserve_movement,amount) AND direction IN ('익금불산입','손금산입')) OR
       (disposition NOT IN ('유보','△유보') AND decimal_sign(reserve_movement)=0)),
 UNIQUE(workpaper_id,row_no), UNIQUE(company_id,workpaper_id,asset_id,adjustment_row_id),
 FOREIGN KEY(company_id,workpaper_id) REFERENCES tax_workpapers(company_id,workpaper_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,asset_id) REFERENCES investment_assets(company_id,asset_id) ON DELETE RESTRICT
);
CREATE TABLE workpaper_decisions (
 decision_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, workpaper_id INTEGER NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('APPROVE','CANCEL')), cancel_of_decision_id INTEGER,
 actor TEXT NOT NULL CHECK(length(trim(actor))>0), reason TEXT NOT NULL CHECK(length(trim(reason))>0),
 acknowledged INTEGER NOT NULL CHECK(acknowledged IN (0,1)), source_revision INTEGER NOT NULL CHECK(source_revision>=0),
 proof_json TEXT NOT NULL CHECK(json_valid(proof_json)), approved_rows_json TEXT CHECK(approved_rows_json IS NULL OR json_valid(approved_rows_json)), created_at TEXT NOT NULL,
 CHECK((action='APPROVE' AND cancel_of_decision_id IS NULL AND acknowledged=1 AND approved_rows_json IS NOT NULL) OR
       (action='CANCEL' AND cancel_of_decision_id IS NOT NULL)),
 UNIQUE(company_id,workpaper_id,decision_id),
 FOREIGN KEY(company_id,workpaper_id) REFERENCES tax_workpapers(company_id,workpaper_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,workpaper_id,cancel_of_decision_id) REFERENCES workpaper_decisions(company_id,workpaper_id,decision_id) ON DELETE RESTRICT,
 CHECK(cancel_of_decision_id IS NULL OR cancel_of_decision_id<>decision_id)
);
CREATE UNIQUE INDEX decision_approval ON workpaper_decisions(workpaper_id) WHERE action='APPROVE';
CREATE UNIQUE INDEX decision_cancellation ON workpaper_decisions(cancel_of_decision_id) WHERE action='CANCEL';
CREATE TABLE workpaper_events (
 event_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, workpaper_id INTEGER NOT NULL, decision_id INTEGER,
 event_type TEXT NOT NULL, detail_json TEXT NOT NULL CHECK(json_valid(detail_json)), actor TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(company_id,workpaper_id) REFERENCES tax_workpapers(company_id,workpaper_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,workpaper_id,decision_id) REFERENCES workpaper_decisions(company_id,workpaper_id,decision_id) ON DELETE RESTRICT
);

CREATE TABLE reserve_entries (
 entry_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, fiscal_year_id INTEGER NOT NULL, asset_id INTEGER NOT NULL,
 workpaper_id INTEGER NOT NULL, decision_id INTEGER NOT NULL, adjustment_row_id INTEGER NOT NULL,
 entry_type TEXT NOT NULL CHECK(entry_type IN ('POST','REVERSAL')), reversal_of_entry_id INTEGER,
 adjustment_key TEXT NOT NULL, amount TEXT NOT NULL CHECK(decimal_valid(amount) AND decimal_sign(amount)<>0),
 memo TEXT NOT NULL, recorded_at TEXT NOT NULL, legacy_source_ref TEXT,
 CHECK((entry_type='POST' AND reversal_of_entry_id IS NULL) OR (entry_type='REVERSAL' AND reversal_of_entry_id IS NOT NULL)),
 UNIQUE(decision_id,adjustment_row_id), UNIQUE(company_id,fiscal_year_id,asset_id,adjustment_row_id,entry_id),
 FOREIGN KEY(company_id,fiscal_year_id,workpaper_id) REFERENCES tax_workpapers(company_id,fiscal_year_id,workpaper_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,workpaper_id,decision_id) REFERENCES workpaper_decisions(company_id,workpaper_id,decision_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,workpaper_id,asset_id,adjustment_row_id) REFERENCES tax_adjustment_rows(company_id,workpaper_id,asset_id,adjustment_row_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,fiscal_year_id,asset_id,adjustment_row_id,reversal_of_entry_id) REFERENCES reserve_entries(company_id,fiscal_year_id,asset_id,adjustment_row_id,entry_id) ON DELETE RESTRICT,
 CHECK(reversal_of_entry_id IS NULL OR reversal_of_entry_id<>entry_id)
);
CREATE UNIQUE INDEX reserve_reversal ON reserve_entries(reversal_of_entry_id) WHERE reversal_of_entry_id IS NOT NULL;
CREATE INDEX reserve_balance ON reserve_entries(company_id,fiscal_year_id,asset_id,adjustment_key);
CREATE TABLE fiscal_closes (
 close_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, fiscal_year_id INTEGER NOT NULL, run_id INTEGER, validation_id INTEGER,
 prior_close_id INTEGER, source_revision INTEGER NOT NULL CHECK(source_revision>=0), evidence_state TEXT NOT NULL CHECK(evidence_state IN ('VERIFIED','LEGACY')),
 opening_json TEXT NOT NULL CHECK(json_valid(opening_json)), movements_json TEXT NOT NULL CHECK(json_valid(movements_json)), ending_json TEXT NOT NULL CHECK(json_valid(ending_json)),
 positions_json TEXT CHECK(positions_json IS NULL OR json_valid(positions_json)), policy_snapshot_json TEXT CHECK(policy_snapshot_json IS NULL OR json_valid(policy_snapshot_json)),
 proof_json TEXT NOT NULL CHECK(json_valid(proof_json)), reviewer TEXT NOT NULL, note TEXT NOT NULL, closed_at TEXT NOT NULL,
 CHECK(evidence_state='LEGACY' OR (run_id IS NOT NULL AND validation_id IS NOT NULL AND positions_json IS NOT NULL AND policy_snapshot_json IS NOT NULL)),
 UNIQUE(company_id,fiscal_year_id), UNIQUE(company_id,close_id), UNIQUE(company_id,fiscal_year_id,close_id),
 FOREIGN KEY(company_id,fiscal_year_id) REFERENCES fiscal_years(company_id,fiscal_year_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,fiscal_year_id,run_id) REFERENCES calculation_runs(company_id,fiscal_year_id,run_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,fiscal_year_id,validation_id) REFERENCES validation_runs(company_id,fiscal_year_id,validation_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,fiscal_year_id,run_id,validation_id) REFERENCES validation_runs(company_id,fiscal_year_id,run_id,validation_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,prior_close_id) REFERENCES fiscal_closes(company_id,close_id) ON DELETE RESTRICT,
 CHECK(prior_close_id IS NULL OR prior_close_id<>close_id)
);
CREATE UNIQUE INDEX close_carryforward ON fiscal_closes(prior_close_id) WHERE prior_close_id IS NOT NULL;
CREATE TABLE closing_events (
 event_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, fiscal_year_id INTEGER NOT NULL, close_id INTEGER,
 event_type TEXT NOT NULL, detail_json TEXT NOT NULL CHECK(json_valid(detail_json)), actor TEXT NOT NULL, created_at TEXT NOT NULL,
 CHECK(event_type<>'CLOSE' OR close_id IS NOT NULL),
 FOREIGN KEY(company_id,fiscal_year_id) REFERENCES fiscal_years(company_id,fiscal_year_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,fiscal_year_id,close_id) REFERENCES fiscal_closes(company_id,fiscal_year_id,close_id) ON DELETE RESTRICT
);

CREATE TABLE audit_log (
 audit_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies ON DELETE RESTRICT,
 source_revision INTEGER NOT NULL CHECK(source_revision>=0), entity_type TEXT NOT NULL, entity_key TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('CREATE','UPDATE','VOID','SELECT_VERSION','IMPORT')),
 before_json TEXT CHECK(before_json IS NULL OR json_valid(before_json)), after_json TEXT CHECK(after_json IS NULL OR json_valid(after_json)),
 actor TEXT NOT NULL, reason TEXT, recorded_at TEXT NOT NULL
);
CREATE INDEX audit_revision ON audit_log(company_id,source_revision);
CREATE INDEX audit_entity ON audit_log(company_id,entity_type,entity_key,recorded_at);
CREATE TABLE legacy_archives (
 archive_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies ON DELETE RESTRICT,
 source_revision INTEGER, backup_hash TEXT NOT NULL, source_table TEXT NOT NULL, source_key TEXT NOT NULL,
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), archived_at TEXT NOT NULL,
 UNIQUE(backup_hash,source_table,source_key), UNIQUE(company_id,archive_id)
);
CREATE TABLE migration_id_map (
 map_id INTEGER PRIMARY KEY, archive_id INTEGER NOT NULL, company_id INTEGER NOT NULL, source_entity TEXT NOT NULL, source_key TEXT NOT NULL,
 target_table TEXT, target_key TEXT, mapping_state TEXT NOT NULL CHECK(mapping_state IN ('MAPPED','REVIEW_REQUIRED')), note TEXT, created_at TEXT NOT NULL,
 CHECK(mapping_state='REVIEW_REQUIRED' OR (target_table IS NOT NULL AND target_key IS NOT NULL)),
 UNIQUE(archive_id,source_entity,source_key,target_table,target_key),
 FOREIGN KEY(company_id,archive_id) REFERENCES legacy_archives(company_id,archive_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX migration_unresolved ON migration_id_map(archive_id,source_entity,source_key) WHERE mapping_state='REVIEW_REQUIRED';
CREATE TABLE legacy_manual_adjustments (
 legacy_adjustment_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, archive_id INTEGER NOT NULL, legacy_key TEXT NOT NULL,
 asset_id INTEGER, date TEXT CHECK(date IS NULL OR date_valid(date)), payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 mapping_state TEXT NOT NULL CHECK(mapping_state IN ('MAPPED','REVIEW_REQUIRED')), created_at TEXT NOT NULL,
 is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0,1)), status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','VOID')),
 UNIQUE(company_id,archive_id,legacy_key),
 FOREIGN KEY(company_id,archive_id) REFERENCES legacy_archives(company_id,archive_id) ON DELETE RESTRICT,
 FOREIGN KEY(company_id,asset_id) REFERENCES investment_assets(company_id,asset_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX legacy_adjustment_current ON legacy_manual_adjustments(company_id,legacy_key) WHERE is_current=1;
