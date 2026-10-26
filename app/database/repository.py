"""Single-company HTTP compatibility adapter over normalized source tables.

Per-record evidence preserves original input metadata; core values are read
from SQL columns. There is deliberately no workspace/payload shadow table.
"""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from . import APPLICATION_ID

COMPANY=1
COLLECTIONS=('master','assets','trades','valuations','adjustments','taxPolicyYears','fvociPolicyYears')
NORMALIZED=set(COLLECTIONS)|{'taxPolicy','fvociPolicy','taxAdjustmentScope','fiscalEndMonth'}


def now(): return datetime.now(timezone.utc).isoformat()
def dumps(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def loads(value): return json.loads(value) if value else {}
def digest(value): return hashlib.sha256(dumps(value).encode()).hexdigest()
def is_normalized(c): return c.execute('PRAGMA application_id').fetchone()[0]==APPLICATION_ID


def insert(c,table,**values):
    return c.execute(f'INSERT INTO {table} ({",".join(values)}) VALUES ({",".join("?" for _ in values)})',tuple(values.values())).lastrowid


def decimal_text(value):
    try:number=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError) as exc:raise ValueError('유효한 금액이 필요합니다.') from exc
    if not number.is_finite():raise ValueError('유효한 금액이 필요합니다.')
    return format(number if number else Decimal(0),'f')


def numeric(value,original):
    if isinstance(original,str):return value
    return int(Decimal(value)) if isinstance(original,int) else float(value)


def period(c,year,month):
    from app.server.validation_engine import year_end
    year=int(year)
    row=c.execute('SELECT fiscal_year_id FROM fiscal_years WHERE company_id=? AND fiscal_year=?',(COMPANY,year)).fetchone()
    if row:return row[0]
    end=year_end(year,month).isoformat()
    start=f'{year-1 if month<12 else year:04d}-{month%12+1:02d}-01'
    return insert(c,'fiscal_years',company_id=COMPANY,fiscal_year=year,start_date=start,end_date=end,status='OPEN',created_at=now())


def stock(c,code,name='',market='UNKNOWN'):
    row=c.execute('SELECT stock_id FROM stock_master WHERE stock_code=? AND is_current_code=1',(str(code),)).fetchone()
    if row:return row[0]
    return insert(c,'stock_master',stock_code=str(code),asset_name=name or str(code),market_type=market or 'UNKNOWN',
                  listing_status='UNKNOWN',is_current_code=1,source='USER',created_at=now(),updated_at=now())


def source_record(row,kind):
    raw=loads(row['evidence_json'])
    result=dict(raw)
    result.update(id=int(row['logical_key'].split(':')[-1]),assetId=row['asset_id'],date=row['trade_date' if kind=='trade' else 'valuation_date'])
    fields={'qty':'quantity','price':'execution_price','amount':'gross_amount'} if kind=='trade' else {'qty':'quantity','price':'applied_price','fairValue':'fair_value'}
    for api,sql in fields.items():
        if api in raw and row[sql] is not None:result[api]=row[sql] if api=='qty' else numeric(row[sql],raw[api])
    if kind=='trade':result['type']=row['event_type']
    return result


def policy_record(row,kind):
    raw=loads(row['evidence_json'] if kind=='tax' else row['metadata_json'])
    result=dict(raw)
    if row['effective_start_year'] is not None:result['effectiveYear']=str(row['effective_start_year']) if isinstance(raw.get('effectiveYear'),str) else row['effective_start_year']
    if kind=='fvoci':result['disposal']=row['disposal']
    else:
        for api,sql in {'method':'method','filingStatus':'filing_status','filingDate':'filing_date','receipt':'receipt',
                        'changeStatus':'change_status','previousMethod':'previous_method'}.items():
            if api in raw:result[api]=row[sql] if row[sql] is not None else raw[api]
    for key in ('reviewer','reason'):
        if key in raw:result[key]=row[key] if row[key] is not None else raw[key]
    return result


def read_state(c):
    company=c.execute('SELECT * FROM companies WHERE company_id=?',(COMPANY,)).fetchone()
    if not company:return dict(revision=0,state=None,updated_at=None,backend='normalized')
    settings=loads(company['settings_json']);keys=set(settings['keys']);state=dict(settings['extra'])
    if 'fiscalEndMonth' in keys:state['fiscalEndMonth']=company['fiscal_end_month']
    state['master']=[]
    for sid in settings.get('master_stock_ids',[]):
        row=c.execute('SELECT * FROM stock_master WHERE stock_id=?',(sid,)).fetchone()
        item=dict(settings.get('master_extra',{}).get(str(sid),{}));item.update(code=row['stock_code'],name=row['asset_name'])
        if settings.get('master_has_market',{}).get(str(sid),True):item['market']=row['market_type']
        state['master'].append(item)
    state['assets']=[]
    for row in c.execute('SELECT a.*,s.stock_code FROM investment_assets a JOIN stock_master s ON s.stock_id=a.stock_id WHERE a.company_id=? AND a.status=\'ACTIVE\' ORDER BY a.asset_id',(COMPANY,)):
        item=loads(row['metadata_json']);item.update(id=row['asset_id'],stock_code=row['stock_code'],classification=row['classification'])
        state['assets'].append(item)
    order=settings.get('order',{}).get('assets',[])
    state['assets'].sort(key=lambda r:order.index(r['id']) if r['id'] in order else r['id'])
    for name,kind in (('trades','trade'),('valuations','valuation')):
        table='trade_events' if kind=='trade' else 'valuation_events'
        state[name]=[source_record(r,kind) for r in c.execute(f"SELECT * FROM {table} WHERE company_id=? AND is_current=1 AND status='ACTIVE' ORDER BY logical_key",(COMPANY,))]
        order=settings.get('order',{}).get(name,[])
        state[name].sort(key=lambda r:order.index(r['id']) if r['id'] in order else r['id'])
    state['adjustments']=[loads(r['payload_json']) for r in c.execute("SELECT * FROM legacy_manual_adjustments WHERE company_id=? AND is_current=1 AND status='ACTIVE' ORDER BY legacy_adjustment_id",(COMPANY,))]
    for table,kind,baseline,array in (('tax_cost_policy_versions','tax','taxPolicy','taxPolicyYears'),('fvoci_policy_versions','fvoci','fvociPolicy','fvociPolicyYears')):
        rows=c.execute(f'SELECT * FROM {table} WHERE company_id=? AND is_current=1 ORDER BY effective_start_year',(COMPANY,)).fetchall()
        if baseline in keys:
            row=next((r for r in rows if r['is_legacy_baseline']),None)
            state[baseline]=policy_record(row,kind) if row else {}
        if array in keys:
            state[array]=[policy_record(r,kind) for r in rows if not r['is_legacy_baseline']]
            order=settings.get('order',{}).get(array,[])
            state[array].sort(key=lambda r:order.index(str(r['effectiveYear'])) if str(r['effectiveYear']) in order else int(r['effectiveYear']))
    if 'taxAdjustmentScope' in keys:
        row=c.execute('SELECT * FROM company_rule_reviews WHERE company_id=? AND is_current=1 ORDER BY review_id DESC LIMIT 1',(COMPANY,)).fetchone()
        scope=loads(row['metadata_json']) if row else {}
        if row:scope.update(enabled=bool(row['enabled']),confirmed=bool(row['confirmed']),reviewer=row['reviewer'] or '',note=row['reason'] or '')
        state['taxAdjustmentScope']=scope
    return dict(state=state,revision=company['source_revision'],updated_at=company['updated_at'],backend='normalized')


def sync_policies(c,state,before):
    for table,kind,baseline,array in (('tax_cost_policy_versions','tax','taxPolicy','taxPolicyYears'),('fvoci_policy_versions','fvoci','fvociPolicy','fvociPolicyYears')):
        incoming={None:state[baseline]} if state.get(baseline) else {}
        incoming.update({int(r['effectiveYear']):r for r in state.get(array,[])})
        current={r['effective_start_year']:r for r in c.execute(f'SELECT * FROM {table} WHERE company_id=? AND is_current=1',(COMPANY,))}
        for year in current.keys()|incoming.keys():
            old=current.get(year);raw=incoming.get(year)
            if old and raw is not None and policy_record(old,kind)==raw:continue
            if old:c.execute(f'UPDATE {table} SET is_current=0 WHERE policy_id=?',(old['policy_id'],))
            if raw is None:continue
            values=dict(company_id=COMPANY,effective_start_year=year,is_legacy_baseline=int(year is None),
                        reviewer=raw.get('reviewer') or (raw.get('evidence') if kind=='tax' else None),
                        reason=raw.get('reason') or (raw.get('evidence') if kind=='tax' else None),
                        version=old['version']+1 if old else 1,is_current=1,supersedes_id=old['policy_id'] if old else None,created_at=now())
            if year is not None and (not values['reviewer'] or not values['reason']):
                if kind=='fvoci':raise ValueError('정책 확인자와 사유가 필요합니다.')
                # Existing tax-policy UI carries evidence rather than reviewer/reason.
                values['reviewer']='local-user';values['reason']=raw.get('evidence') or '신고·변경 요건은 별도 검토가 필요한 입력 정책'
            if kind=='fvoci':values.update(disposal=raw['disposal'],confirmation_status='CONFIRMED' if year is not None else 'UNKNOWN',metadata_json=dumps(raw))
            else:values.update(method=raw.get('method','TOTAL'),filing_status=raw.get('filingStatus','UNKNOWN'),
                               filing_date=raw.get('filingDate') or None,receipt=raw.get('receipt'),change_status=raw.get('changeStatus','NO'),
                               previous_method=raw.get('previousMethod') or None,evidence_json=dumps(raw))
            insert(c,table,**values)


def save_state(c,state,expected_revision):
    from app.server.closing import guard_state_changes
    from app.server.validation_engine import fiscal_year
    for collection in ('assets','trades','valuations'):
        identifiers=[r.get('id') if isinstance(r,dict) else None for r in state[collection]]
        if any(type(i) is not int or i<1 for i in identifiers) or len(set(identifiers))!=len(identifiers):
            raise ValueError(f'{collection}: 양의 정수 ID가 필요하며 중복은 허용하지 않습니다.')
    for name in ('taxPolicyYears',):
        years=[str(r.get('effectiveYear')) for r in state[name]]
        if len(set(years))!=len(years):raise ValueError('중복 적용연도는 허용하지 않습니다.')
    current=read_state(c);before=current['state'] or {};revision=current['revision']
    if revision!=expected_revision:raise RuntimeError(f'저장 충돌: 서버 버전 {revision}, 요청 버전 {expected_revision}')
    guard_state_changes(before,state,c)
    month=int(state.get('fiscalEndMonth') or 12)
    if before and int(before.get('fiscalEndMonth') or 12)!=month and c.execute('SELECT 1 FROM fiscal_years WHERE company_id=? LIMIT 1',(COMPANY,)).fetchone():
        raise ValueError('사업연도 자료가 이미 있어 결산월은 별도 기간 재편 절차가 필요합니다.')
    stamp=now()
    if not before:
        insert(c,'companies',company_id=COMPANY,company_name='미확인 로컬 작업공간',fiscal_end_month=month,source_revision=0,created_at=stamp,updated_at=stamp)
    master_ids=[];master_extra={};master_has_market={}
    for m in state['master']:
        sid=stock(c,m['code'],m['name'],m.get('market','UNKNOWN'));master_ids.append(sid)
        if master_ids.count(sid)>1:raise ValueError('중복 종목코드는 허용하지 않습니다.')
        master_has_market[str(sid)]='market' in m
        c.execute('UPDATE stock_master SET asset_name=?,market_type=?,updated_at=? WHERE stock_id=?',(m['name'],m.get('market','UNKNOWN'),stamp,sid))
        master_extra[str(sid)]={k:v for k,v in m.items() if k not in ('code','name','market')}
    old_assets={a['id']:a for a in before.get('assets',[])};new_assets={a['id']:a for a in state['assets']}
    for aid,asset in new_assets.items():
        sid=stock(c,asset['stock_code'],asset.get('asset_name',''))
        method=state.get('stockPolicies',{}).get(asset['stock_code'],{}).get('book','TOTAL')
        metadata=dumps({k:v for k,v in asset.items() if k not in ('id','stock_code','classification')})
        if c.execute('SELECT 1 FROM investment_assets WHERE company_id=? AND asset_id=?',(COMPANY,aid)).fetchone():
            c.execute("UPDATE investment_assets SET stock_id=?,classification=?,book_cost_method=?,metadata_json=?,status='ACTIVE',updated_at=? WHERE company_id=? AND asset_id=?",(sid,asset['classification'],method,metadata,stamp,COMPANY,aid))
        else:insert(c,'investment_assets',asset_id=aid,company_id=COMPANY,stock_id=sid,classification=asset['classification'],book_cost_method=method,metadata_json=metadata,created_at=stamp,updated_at=stamp)
    for name,kind,table,idcol in (('trades','trade','trade_events','trade_id'),('valuations','valuation','valuation_events','valuation_id')):
        old={r['id']:r for r in before.get(name,[])};incoming={r['id']:r for r in state[name]}
        for identifier in old.keys()|incoming.keys():
            raw=incoming.get(identifier)
            if old.get(identifier)==raw:continue
            logical=f'{kind}:{identifier}'
            previous=c.execute(f'SELECT * FROM {table} WHERE company_id=? AND logical_key=? AND is_current=1',(COMPANY,logical)).fetchone()
            if previous:c.execute(f'UPDATE {table} SET is_current=0 WHERE {idcol}=?',(previous[idcol],))
            record=raw or old[identifier]
            values=dict(company_id=COMPANY,asset_id=record['assetId'],fiscal_year_id=period(c,fiscal_year(record['date'],month),month),
                        logical_key=logical,version=previous['version']+1 if previous else 1,is_current=1,
                        supersedes_id=previous[idcol] if previous else None,status='ACTIVE' if raw else 'VOID',created_at=stamp,evidence_json=dumps(record))
            if kind=='trade':values.update(trade_date=record['date'],event_type=record['type'],quantity=record.get('qty') if record['type']!='DIVIDEND' else None,
                                           execution_price=decimal_text(record['price']) if record.get('price') is not None else None,
                                           gross_amount=decimal_text(record['amount']),note=record.get('note'),
                                           **{sql:decimal_text(record[api]) if record.get(api) is not None else None for api,sql in
                                              (('fees','fees'),('transaction_tax','transaction_tax'),('net_cash_amount','net_cash_amount'))})
            else:values.update(valuation_date=record['date'],quantity=record['qty'],applied_price=decimal_text(record['price']),
                               fair_value=decimal_text(record['fairValue']),price_date=record.get('priceDate') or record.get('price_date') or record['date'],
                               source=record.get('source') or 'USER',note=record.get('note'))
            insert(c,table,**values)
    for aid in old_assets.keys()-new_assets.keys():c.execute("UPDATE investment_assets SET status='ARCHIVED',updated_at=? WHERE company_id=? AND asset_id=?",(stamp,COMPANY,aid))
    sync_policies(c,state,before)
    scope=state.get('taxAdjustmentScope')
    if scope and scope!=before.get('taxAdjustmentScope'):
        year_id=period(c,scope['year'],month)
        previous=c.execute('SELECT * FROM company_rule_reviews WHERE company_id=? AND fiscal_year_id=? AND is_current=1',(COMPANY,year_id)).fetchone()
        if previous:c.execute('UPDATE company_rule_reviews SET is_current=0 WHERE review_id=?',(previous['review_id'],))
        insert(c,'company_rule_reviews',company_id=COMPANY,fiscal_year_id=year_id,rule_version=scope.get('profile','MANUAL'),
               enabled=int(scope.get('enabled') is True),confirmed=int(scope.get('confirmed') is True),reviewer=scope.get('reviewer'),reason=scope.get('note'),
               exclusions_json=dumps(scope.get('exceptions',[])),legal_sources_json='[]',checked_on=stamp[:10],
               version=previous['version']+1 if previous else 1,is_current=1,supersedes_id=previous['review_id'] if previous else None,created_at=stamp,metadata_json=dumps(scope))
    elif not scope and before.get('taxAdjustmentScope'):
        c.execute('UPDATE company_rule_reviews SET is_current=0 WHERE company_id=? AND is_current=1',(COMPANY,))
    old_adj={str(a.get('id',digest(a))):a for a in before.get('adjustments',[])};new_adj={str(a.get('id',digest(a))):a for a in state['adjustments']}
    for key in old_adj.keys()|new_adj.keys():
        if old_adj.get(key)==new_adj.get(key):continue
        raw=new_adj.get(key) or old_adj[key]
        c.execute('UPDATE legacy_manual_adjustments SET is_current=0 WHERE company_id=? AND legacy_key=?',(COMPANY,key))
        archive=insert(c,'legacy_archives',company_id=COMPANY,source_revision=revision+1,backup_hash=digest([revision+1,key,raw]),source_table='state.adjustments',source_key=key,payload_json=dumps(raw),archived_at=stamp)
        insert(c,'legacy_manual_adjustments',company_id=COMPANY,archive_id=archive,legacy_key=key,asset_id=raw.get('assetId'),date=raw.get('date'),
               payload_json=dumps(raw),mapping_state='REVIEW_REQUIRED',created_at=stamp,is_current=1,status='ACTIVE' if key in new_adj else 'VOID')
    settings=dict(keys=list(state),extra={k:v for k,v in state.items() if k not in NORMALIZED},master_stock_ids=master_ids,master_extra=master_extra,master_has_market=master_has_market,
                  order={name:[r['id'] for r in state[name]] for name in ('assets','trades','valuations')})
    settings['order'].update({name:[str(r['effectiveYear']) for r in state.get(name,[])] for name in ('taxPolicyYears','fvociPolicyYears')})
    c.execute('UPDATE companies SET fiscal_end_month=?,source_revision=?,settings_json=?,updated_at=? WHERE company_id=?',(month,revision+1,dumps(settings),stamp,COMPANY))
    from app.server.storage import _records
    left,right=_records(before),_records(state);count=0
    for entity,key in sorted(left.keys()|right.keys()):
        old,new=left.get((entity,key)),right.get((entity,key))
        if old==new:continue
        insert(c,'audit_log',company_id=COMPANY,source_revision=revision+1,entity_type=entity,entity_key=key,
               action='VOID' if (entity,key) not in right else 'CREATE' if (entity,key) not in left else 'UPDATE',
               before_json=dumps(old) if old is not None else None,after_json=dumps(new) if new is not None else None,
               actor='local-user',reason='normalized source save',recorded_at=stamp)
        count+=1
    return dict(revision=revision+1,audit_events=count)
