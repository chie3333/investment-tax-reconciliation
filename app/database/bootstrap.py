"""Create and inspect normalized SQLite databases without overwriting files."""
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path

from . import APPLICATION_ID, SCHEMA_VERSION

SCHEMA_PATH = Path(__file__).with_name('schema.sql')
DEFAULT_PATH = Path(__file__).resolve().parents[2] / 'data' / 'investment.sqlite3'
DECIMAL_PATTERN = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z')


def decimal_valid(value):
    return int(isinstance(value,str) and len(value)<=128 and bool(DECIMAL_PATTERN.fullmatch(value))
               and not (value.startswith('-') and Decimal(value)==0))


def decimal_sign(value):
    if not decimal_valid(value):
        return None
    number=Decimal(value)
    return int(number>0)-int(number<0)


def decimal_equal(left,right):
    return int(bool(decimal_valid(left) and decimal_valid(right)) and Decimal(left)==Decimal(right))


def decimal_difference(left,right,result):
    if not all(decimal_valid(v) for v in (left,right,result)):
        return 0
    with localcontext() as context:
        context.prec=300
        return int(Decimal(left)-Decimal(right)==Decimal(result))


def date_valid(value):
    try:
        return int(isinstance(value,str) and date.fromisoformat(value).isoformat()==value)
    except (ValueError,TypeError):
        return 0


def configure(connection,read_only=False):
    connection.row_factory=sqlite3.Row
    for name,function,nargs in (('decimal_valid',decimal_valid,1),('decimal_sign',decimal_sign,1),
                                ('decimal_equal',decimal_equal,2),('decimal_difference',decimal_difference,3),
                                ('date_valid',date_valid,1)):
        connection.create_function(name,nargs,function,deterministic=True)
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA busy_timeout=5000')
    if read_only:
        connection.execute('PRAGMA query_only=ON')
    return connection


def connect_database(path=DEFAULT_PATH,read_only=False):
    """Open an existing file only. Callers own/close the connection."""
    path=Path(path).resolve()
    connection=sqlite3.connect(path.as_uri()+('?mode=ro' if read_only else '?mode=rw'),uri=True)
    try:
        return configure(connection,read_only)
    except BaseException:
        connection.close()
        raise


def quote(identifier):
    return '"'+identifier.replace('"','""')+'"'


def object_hash(connection):
    objects=[tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
    return hashlib.sha256(json.dumps(objects,ensure_ascii=False).encode()).hexdigest()


def add_triggers(c):
    # These are structural guards, not the still-unimplemented normalized services.
    tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for table in tables:
        integers=[r['name'] for r in c.execute(f'PRAGMA table_info({quote(table)})') if r['type']=='INTEGER']
        condition=' OR '.join(f'(NEW.{quote(col)} IS NOT NULL AND typeof(NEW.{quote(col)})<>\'integer\')' for col in integers)
        if condition:
            for operation in ('INSERT','UPDATE'):
                c.execute(f'''CREATE TRIGGER {table}_integers_{operation.lower()} BEFORE {operation} ON {table}
                  WHEN {condition} BEGIN SELECT RAISE(ABORT,'integer columns require integer values'); END''')
    immutable=('schema_metadata','market_prices','rounding_profiles','calculation_runs','calculated_balances',
               'calculated_stock_pools','validation_runs','issue_review_events','workpaper_decisions',
               'workpaper_events','reserve_entries','fiscal_closes','closing_events','audit_log','legacy_archives')
    for table in immutable:
        for operation in ('UPDATE','DELETE'):
            c.execute(f'''CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table}
                          BEGIN SELECT RAISE(ABORT,'immutable evidence: append a new fact'); END''')
    for operation in ('INSERT','UPDATE'):
        exclusion='AND fiscal_year_id<>OLD.fiscal_year_id' if operation=='UPDATE' else ''
        c.execute(f'''CREATE TRIGGER fiscal_period_overlap_{operation.lower()} BEFORE {operation} ON fiscal_years
          WHEN EXISTS(SELECT 1 FROM fiscal_years WHERE company_id=NEW.company_id {exclusion}
                      AND start_date<=NEW.end_date AND end_date>=NEW.start_date)
          BEGIN SELECT RAISE(ABORT,'overlapping fiscal periods'); END''')
    c.execute('''CREATE TRIGGER fiscal_period_insert_open BEFORE INSERT ON fiscal_years WHEN NEW.status<>'OPEN'
      BEGIN SELECT RAISE(ABORT,'insert an open period before its closing fact'); END''')
    c.execute('''CREATE TRIGGER fiscal_period_frozen BEFORE UPDATE ON fiscal_years
      WHEN EXISTS(SELECT 1 FROM fiscal_closes WHERE fiscal_year_id=OLD.fiscal_year_id)
      AND (NEW.company_id<>OLD.company_id OR NEW.fiscal_year<>OLD.fiscal_year OR NEW.start_date<>OLD.start_date
           OR NEW.end_date<>OLD.end_date OR NEW.status<>'CLOSED')
      BEGIN SELECT RAISE(ABORT,'closed fiscal period'); END''')
    c.execute('''CREATE TRIGGER fiscal_status_needs_close BEFORE UPDATE OF status ON fiscal_years
      WHEN NEW.status='CLOSED' AND NOT EXISTS(SELECT 1 FROM fiscal_closes WHERE fiscal_year_id=NEW.fiscal_year_id)
      BEGIN SELECT RAISE(ABORT,'a closing fact is required'); END''')
    c.execute('''CREATE TRIGGER close_period AFTER INSERT ON fiscal_closes
      BEGIN UPDATE fiscal_years SET status='CLOSED' WHERE fiscal_year_id=NEW.fiscal_year_id; END''')
    for table,date_column,key in (('trade_events','trade_date','trade_id'),('valuation_events','valuation_date','valuation_id')):
        for operation in ('INSERT','UPDATE','DELETE'):
            refs=('OLD','NEW') if operation=='UPDATE' else (('NEW',) if operation=='INSERT' else ('OLD',))
            conditions=[]
            for ref in refs:
                conditions.append(f"EXISTS(SELECT 1 FROM fiscal_years WHERE fiscal_year_id={ref}.fiscal_year_id AND status='CLOSED')")
            c.execute(f'''CREATE TRIGGER {table}_closed_{operation.lower()} BEFORE {operation} ON {table}
              WHEN {' OR '.join(conditions)} BEGIN SELECT RAISE(ABORT,'source belongs to closed period'); END''')
        c.execute(f'''CREATE TRIGGER {table}_period BEFORE INSERT ON {table}
          WHEN NOT EXISTS(SELECT 1 FROM fiscal_years WHERE company_id=NEW.company_id AND fiscal_year_id=NEW.fiscal_year_id
                          AND NEW.{date_column} BETWEEN start_date AND end_date)
          BEGIN SELECT RAISE(ABORT,'source date outside fiscal period'); END''')
        c.execute(f'''CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
          BEGIN SELECT RAISE(ABORT,'source history cannot be deleted'); END''')
        columns=[r['name'] for r in c.execute(f'PRAGMA table_info({table})') if r['name']!='is_current']
        condition=' OR '.join(f'NEW.{quote(col)} IS NOT OLD.{quote(col)}' for col in columns)
        c.execute(f'''CREATE TRIGGER {table}_version_content BEFORE UPDATE ON {table} WHEN {condition}
          BEGIN SELECT RAISE(ABORT,'source correction requires a new version'); END''')
        c.execute(f'''CREATE TRIGGER {table}_predecessor BEFORE INSERT ON {table}
          WHEN (NEW.supersedes_id IS NULL AND NEW.version<>1) OR (NEW.supersedes_id IS NOT NULL
          AND NOT EXISTS(SELECT 1 FROM {table} p JOIN fiscal_years y ON y.fiscal_year_id=p.fiscal_year_id
                         WHERE p.{key}=NEW.supersedes_id AND p.company_id=NEW.company_id AND p.logical_key=NEW.logical_key
                         AND p.asset_id=NEW.asset_id AND NEW.version=p.version+1 AND y.status='OPEN'))
          BEGIN SELECT RAISE(ABORT,'invalid source predecessor'); END''')
    c.execute('''CREATE TRIGGER valuation_price_stock BEFORE INSERT ON valuation_events
      WHEN NEW.price_id IS NOT NULL AND NOT EXISTS(
        SELECT 1 FROM market_prices p JOIN investment_assets a ON a.stock_id=p.stock_id
        WHERE p.price_id=NEW.price_id AND a.asset_id=NEW.asset_id AND a.company_id=NEW.company_id AND p.price_date=NEW.price_date)
      BEGIN SELECT RAISE(ABORT,'valuation quote belongs to another stock or date'); END''')
    c.execute('''CREATE TRIGGER price_revision_chain BEFORE INSERT ON market_prices
      WHEN (NEW.supersedes_price_id IS NULL AND NEW.quote_revision<>1)
        OR (NEW.supersedes_price_id IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM market_prices WHERE price_id=NEW.supersedes_price_id AND quote_revision+1=NEW.quote_revision))
      BEGIN SELECT RAISE(ABORT,'invalid quote revision predecessor'); END''')
    c.execute('''CREATE TRIGGER asset_economic_identity BEFORE UPDATE ON investment_assets
      WHEN (NEW.company_id<>OLD.company_id OR NEW.stock_id<>OLD.stock_id OR NEW.classification<>OLD.classification
            OR NEW.book_cost_method<>OLD.book_cost_method)
        AND (EXISTS(SELECT 1 FROM trade_events WHERE asset_id=OLD.asset_id) OR EXISTS(SELECT 1 FROM valuation_events WHERE asset_id=OLD.asset_id))
      BEGIN SELECT RAISE(ABORT,'asset identity already has source history'); END''')
    c.execute('''CREATE TRIGGER company_closed_month BEFORE UPDATE OF fiscal_end_month ON companies
      WHEN NEW.fiscal_end_month<>OLD.fiscal_end_month AND EXISTS(SELECT 1 FROM fiscal_closes WHERE company_id=OLD.company_id)
      BEGIN SELECT RAISE(ABORT,'company has closed fiscal periods'); END''')
    for table in ('tax_cost_policy_versions','fvoci_policy_versions'):
        for operation in ('INSERT','UPDATE','DELETE'):
            refs=('OLD','NEW') if operation=='UPDATE' else (('NEW',) if operation=='INSERT' else ('OLD',))
            condition=' OR '.join(f'''EXISTS(SELECT 1 FROM fiscal_closes z JOIN fiscal_years y ON y.fiscal_year_id=z.fiscal_year_id
                 WHERE z.company_id={r}.company_id AND ({r}.is_legacy_baseline=1 OR {r}.effective_start_year<=y.fiscal_year))''' for r in refs)
            c.execute(f'''CREATE TRIGGER {table}_closed_{operation.lower()} BEFORE {operation} ON {table}
              WHEN {condition} BEGIN SELECT RAISE(ABORT,'policy affects a closed fiscal year'); END''')
        columns=[r['name'] for r in c.execute(f'PRAGMA table_info({table})') if r['name']!='is_current']
        condition=' OR '.join(f'NEW.{quote(col)} IS NOT OLD.{quote(col)}' for col in columns)
        c.execute(f'''CREATE TRIGGER {table}_version_content BEFORE UPDATE ON {table} WHEN {condition}
          BEGIN SELECT RAISE(ABORT,'policy correction requires a new version'); END''')
        c.execute(f'''CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
          BEGIN SELECT RAISE(ABORT,'policy history cannot be deleted'); END''')
        c.execute(f'''CREATE TRIGGER {table}_predecessor BEFORE INSERT ON {table}
          WHEN (NEW.supersedes_id IS NULL AND NEW.version<>1) OR (NEW.supersedes_id IS NOT NULL
          AND NOT EXISTS(SELECT 1 FROM {table} p WHERE p.policy_id=NEW.supersedes_id AND p.company_id=NEW.company_id
                         AND p.effective_start_year IS NEW.effective_start_year AND p.is_legacy_baseline=NEW.is_legacy_baseline AND NEW.version=p.version+1))
          BEGIN SELECT RAISE(ABORT,'invalid policy predecessor'); END''')
    for operation in ('INSERT','UPDATE','DELETE'):
        refs=('OLD','NEW') if operation=='UPDATE' else (('NEW',) if operation=='INSERT' else ('OLD',))
        condition=' OR '.join(f"EXISTS(SELECT 1 FROM fiscal_years WHERE fiscal_year_id={r}.fiscal_year_id AND status='CLOSED')" for r in refs)
        c.execute(f'''CREATE TRIGGER rule_review_closed_{operation.lower()} BEFORE {operation} ON company_rule_reviews
          WHEN {condition} BEGIN SELECT RAISE(ABORT,'rule review belongs to a closed period'); END''')
    c.execute('''CREATE TRIGGER workpaper_no_delete BEFORE DELETE ON tax_workpapers
      BEGIN SELECT RAISE(ABORT,'workpaper history cannot be deleted'); END''')
    columns=[r['name'] for r in c.execute('PRAGMA table_info(tax_workpapers)') if r['name']!='status']
    condition=' OR '.join(f'NEW.{quote(col)} IS NOT OLD.{quote(col)}' for col in columns)
    c.execute(f'''CREATE TRIGGER workpaper_approved_content BEFORE UPDATE ON tax_workpapers
      WHEN EXISTS(SELECT 1 FROM workpaper_decisions WHERE workpaper_id=OLD.workpaper_id AND action='APPROVE')
       AND ({condition}) BEGIN SELECT RAISE(ABORT,'approved workpaper evidence is immutable'); END''')
    for operation in ('INSERT','UPDATE','DELETE'):
        refs=('OLD','NEW') if operation=='UPDATE' else (('NEW',) if operation=='INSERT' else ('OLD',))
        condition=' OR '.join(f"EXISTS(SELECT 1 FROM workpaper_decisions WHERE workpaper_id={ref}.workpaper_id AND action='APPROVE')" for ref in refs)
        c.execute(f'''CREATE TRIGGER adjustment_frozen_{operation.lower()} BEFORE {operation} ON tax_adjustment_rows
          WHEN {condition}
          BEGIN SELECT RAISE(ABORT,'approved adjustment rows are immutable'); END''')
    c.execute('''CREATE TRIGGER approval_current_evidence BEFORE INSERT ON workpaper_decisions
      WHEN NEW.action='APPROVE' AND NOT EXISTS(
        SELECT 1 FROM tax_workpapers w JOIN calculation_runs r ON r.run_id=w.run_id
          JOIN companies co ON co.company_id=w.company_id JOIN fiscal_years y ON y.fiscal_year_id=w.fiscal_year_id
          JOIN rounding_profiles p ON p.rounding_profile_id=r.rounding_profile_id
        WHERE w.workpaper_id=NEW.workpaper_id AND w.evidence_state='VERIFIED' AND w.status='DRAFT'
          AND r.status='SUCCEEDED' AND r.source_revision=co.source_revision AND NEW.source_revision=co.source_revision
          AND y.status='OPEN' AND p.status<>'DRAFT'
          AND ((w.trade_id IS NOT NULL AND EXISTS(SELECT 1 FROM trade_events t WHERE t.trade_id=w.trade_id AND t.is_current=1 AND t.status='ACTIVE'))
            OR (w.valuation_id IS NOT NULL AND EXISTS(SELECT 1 FROM valuation_events v WHERE v.valuation_id=w.valuation_id AND v.is_current=1 AND v.status='ACTIVE'))))
      BEGIN SELECT RAISE(ABORT,'approval requires current verified calculation and source'); END''')
    c.execute('''CREATE TRIGGER decision_closed BEFORE INSERT ON workpaper_decisions
      WHEN EXISTS(SELECT 1 FROM tax_workpapers w JOIN fiscal_years y ON y.fiscal_year_id=w.fiscal_year_id
                  WHERE w.workpaper_id=NEW.workpaper_id AND y.status='CLOSED')
      BEGIN SELECT RAISE(ABORT,'decision belongs to a closed period'); END''')
    c.execute('''CREATE TRIGGER cancel_approval_only BEFORE INSERT ON workpaper_decisions
      WHEN NEW.action='CANCEL' AND NOT EXISTS(SELECT 1 FROM workpaper_decisions WHERE decision_id=NEW.cancel_of_decision_id AND action='APPROVE')
      BEGIN SELECT RAISE(ABORT,'cancellation must reference an approval'); END''')
    c.execute('''CREATE TRIGGER reserve_source_amount BEFORE INSERT ON reserve_entries
      WHEN (NEW.entry_type='POST' AND NOT EXISTS(
          SELECT 1 FROM tax_adjustment_rows r JOIN workpaper_decisions d ON d.decision_id=NEW.decision_id
          WHERE r.adjustment_row_id=NEW.adjustment_row_id AND r.disposition IN ('유보','△유보')
            AND decimal_equal(r.reserve_movement,NEW.amount) AND d.action='APPROVE'))
        OR (NEW.entry_type='REVERSAL' AND NOT EXISTS(
          SELECT 1 FROM reserve_entries e JOIN workpaper_decisions d ON d.decision_id=NEW.decision_id
          WHERE e.entry_id=NEW.reversal_of_entry_id AND e.entry_type='POST' AND d.action='CANCEL'
            AND d.cancel_of_decision_id=e.decision_id AND decimal_difference('0',e.amount,NEW.amount)))
      BEGIN SELECT RAISE(ABORT,'invalid reserve posting or reversal'); END''')
    c.execute('''CREATE TRIGGER reserve_closed BEFORE INSERT ON reserve_entries
      WHEN EXISTS(SELECT 1 FROM fiscal_years WHERE fiscal_year_id=NEW.fiscal_year_id AND status='CLOSED')
      BEGIN SELECT RAISE(ABORT,'reserve belongs to a closed period'); END''')
    c.execute('''CREATE TRIGGER close_verified_evidence BEFORE INSERT ON fiscal_closes WHEN NEW.evidence_state='VERIFIED'
      AND NOT EXISTS(SELECT 1 FROM calculation_runs r JOIN validation_runs v ON v.run_id=r.run_id
                     JOIN rounding_profiles p ON p.rounding_profile_id=r.rounding_profile_id
                     WHERE r.run_id=NEW.run_id AND v.validation_id=NEW.validation_id AND r.status='SUCCEEDED'
                       AND v.evidence_state='VERIFIED' AND r.source_revision=NEW.source_revision
                       AND v.source_revision=NEW.source_revision AND p.status<>'DRAFT'
                       AND coalesce(json_extract(v.summary_json,'$.ERROR'),-1)=0)
      BEGIN SELECT RAISE(ABORT,'verified closing requires successful matching evidence'); END''')
    c.execute('''CREATE TRIGGER validation_calculation BEFORE INSERT ON validation_runs WHEN NEW.evidence_state='VERIFIED'
      AND NOT EXISTS(SELECT 1 FROM calculation_runs WHERE run_id=NEW.run_id AND status='SUCCEEDED' AND source_revision=NEW.source_revision)
      BEGIN SELECT RAISE(ABORT,'validation requires matching successful calculation'); END''')
    c.execute('''CREATE TRIGGER balance_calculation BEFORE INSERT ON calculated_balances
      WHEN NOT EXISTS(SELECT 1 FROM calculation_runs WHERE run_id=NEW.run_id AND status='SUCCEEDED')
      BEGIN SELECT RAISE(ABORT,'balance requires successful calculation'); END''')


def inspect_database(path=DEFAULT_PATH):
    path=Path(path).resolve()
    if not path.exists():
        return {'path':str(path),'exists':False}
    c=connect_database(path,read_only=True)
    try:
        c.execute('BEGIN')
        names=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts={name:c.execute(f'SELECT count(*) FROM {quote(name)}').fetchone()[0] for name in names}
        metadata=dict(c.execute('SELECT * FROM schema_metadata WHERE id=1').fetchone()) if 'schema_metadata' in names else None
        return dict(path=str(path),exists=True,application_id=c.execute('PRAGMA application_id').fetchone()[0],
                    user_version=c.execute('PRAGMA user_version').fetchone()[0],tables=counts,
                    user_data_rows=sum(count for name,count in counts.items() if name not in ('schema_metadata','rounding_profiles')),
                    metadata=metadata,object_hash_matches=bool(metadata and metadata['object_hash']==object_hash(c)),
                    integrity=[r[0] for r in c.execute('PRAGMA integrity_check')],
                    foreign_key_errors=[tuple(r) for r in c.execute('PRAGMA foreign_key_check')])
    finally:
        c.close()


def create_database(path=DEFAULT_PATH):
    requested=Path(path)
    if requested.exists() or requested.is_symlink():
        raise FileExistsError('기존 파일은 덮어쓰지 않습니다. 다른 새 경로를 지정하거나 inspect 명령으로 확인하세요.')
    target=requested.resolve()
    target.parent.mkdir(parents=True,exist_ok=True)
    handle,temporary=tempfile.mkstemp(prefix='.investment-v2-',suffix='.sqlite3',dir=target.parent)
    os.close(handle)
    temporary=Path(temporary)
    try:
        c=configure(sqlite3.connect(temporary))
        try:
            sql=SCHEMA_PATH.read_text(encoding='utf-8')
            c.executescript(sql)
            add_triggers(c)
            now=datetime.now(timezone.utc).isoformat()
            c.execute('INSERT INTO rounding_profiles VALUES(?,?,?,?,?,?,?)',('LEGACY_CENT_V1',
                      'Existing 0.01 KRW prototype calculation; not statutory whole-won rounding',
                      json.dumps({'output_scale':2,'rounding':'HALF_AWAY_FROM_ZERO','residual':'LAST_HELD_ASSET'}),
                      'LEGACY',None,None,now))
            c.execute('INSERT INTO schema_metadata VALUES(1,?,?,?,?)',
                      (SCHEMA_VERSION,hashlib.sha256(sql.encode()).hexdigest(),object_hash(c),now))
            c.execute(f'PRAGMA application_id={APPLICATION_ID}')
            c.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            c.commit()
            if list(c.execute('PRAGMA foreign_key_check')) or c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise RuntimeError('New schema integrity check failed')
        finally:
            c.close()
        # Atomic no-clobber publication: even a race cannot overwrite another file.
        os.link(temporary,target)
        return inspect_database(target)
    finally:
        temporary.unlink(missing_ok=True)
