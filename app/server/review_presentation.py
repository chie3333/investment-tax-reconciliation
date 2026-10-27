"""Frozen, explanatory workpaper facts; never used as approval authority."""
from decimal import Decimal
from fractions import Fraction

from .calculation import money,number
from .rounding import profile_for_year,WON,whole
from .validation_engine import fiscal_year


def attach_review_details(state,calculated,items):
    """Build explanations from the same saved sources and canonical replay.

    This leaves the engine digest, adjustment rules and existing history intact.
    Missing explanations on old workpapers are not reconstructed from live data.
    """
    sources={(kind,str(r['id'])):r for name,kind in (('trades','trade'),('valuations','valuation')) for r in state.get(name,[])}
    quantities={};reserves={};books={};oci={};details={}
    month=int(state.get('fiscalEndMonth') or 12)
    for row in calculated['ledger']:
        aid=row['asset_id'];source=sources[(row['source_type'],str(row['source_id']))]
        before_qty=quantities.get(aid,0);after_qty=before_qty
        typ=row['event'];year=fiscal_year(row['date'],month)
        profile=profile_for_year(state,year);scale=0 if profile==WON else 2
        def fmt(value):return money(Fraction(value),scale)
        gross=Fraction(number(source['amount'])) if typ in ('BUY','SELL','DIVIDEND') else None
        if gross is not None and profile==WON:gross=whole(gross)
        if typ=='BUY':after_qty+=int(source['qty'])
        elif typ=='SELL':after_qty-=int(source['qty'])
        prior_reserve=reserves.get(aid,Decimal(0));prior_book=books.get(aid,Decimal(0));prior_oci=oci.get(aid,Decimal(0))
        current_oci=prior_oci+Decimal(row['oci_change'])-Decimal(row['oci_equity_transfer'])
        detail=dict(version='review-v1',rounding_profile=profile,quantity_before=before_qty,quantity_after=after_qty,
                    quantity=before_qty if typ in ('VALUATION','DIVIDEND') else int(source.get('qty') or 0),recorded_quantity=source.get('qty'),book_before=fmt(prior_book),book_after=row['book_basis'],
                    reserve_before=fmt(prior_reserve),reserve_after=fmt(Decimal(row['tax_basis'])-Decimal(row['book_basis'])),
                    oci_before=fmt(prior_oci),oci_after=fmt(current_oci),gross_amount=fmt(gross) if gross is not None else None,
                    input_price=str(source['price']) if source.get('price') is not None else None)
        if typ=='SELL':
            recognition=Decimal(row['oci_change'] if row['classification']=='FVOCI' else row['book_pl'])
            detail.update(book_removed=fmt(gross-Fraction(recognition)),tax_cost=fmt(gross-Fraction(row['tax_pl'])),
                          disposed_prior_oci=fmt(Decimal(row['oci_equity_transfer'])-Decimal(row['oci_change'])) if row['fvoci_disposal_policy']=='TRANSFER' else None)
        details[(row['source_type'],str(row['source_id']),aid)]=detail
        quantities[aid]=after_qty;oci[aid]=current_oci
        for effect in row['allocation_effects']:
            reserves[effect['assetId']]=Decimal(effect['reserve']);books[effect['assetId']]=Decimal(effect['book'])
    for item in items:
        item['review_detail']=details[(item['source_type'],str(item['source_id']),item['asset_id'])]
