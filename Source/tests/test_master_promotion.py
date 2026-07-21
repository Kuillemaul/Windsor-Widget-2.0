from __future__ import annotations
import json
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
import pytest

from windsor_widget.db.base import Base
from windsor_widget.db.models import AppUser, AuditEvent, CustomerAccount, ImportBatch, ImportIssue, ImportRow, Item, Supplier
from windsor_widget.imports.promotion import (
    MasterImportError,
    approve_master_batches,
    ensure_app_user,
    promote_master_batches,
    review_master_batches,
)


def engine():
    e=create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(e)
    return e


def payload(values):
    return json.dumps({'raw_values': list(values.values()), 'values': values})


def add_batch(session, source_type, values, status='staged'):
    batch=ImportBatch(source_type=source_type, source_file_name=f'{source_type}.TXT', file_sha256=source_type*4, status=status, row_count=len(values))
    session.add(batch); session.flush()
    for n, row_values in enumerate(values, start=1):
        session.add(ImportRow(import_batch_id=batch.import_batch_id,row_number=n,raw_text='x',raw_json=payload(row_values),natural_key=str(n),row_sha256=f'{source_type}-{n}',status='accepted' if status=='approved' else 'parsed',issue_count=0))
    session.flush()
    if status=='approved': batch.accepted_row_count=len(values)
    return batch


def complete_values():
    return {
        'supplier_master': [{'Co./Last Name':'Supplier A','Record ID':'S1','Card ID':'SUP1','Card Status':'N','Addr 1 - Email':'s@example.com'}],
        'customer_master': [{'Co./Last Name':'Customer A','Record ID':'C1','Card ID':'CUS1','Card Status':'N','Addr 1 - City':'Melbourne'}],
        'item_master': [{'Item Number':'ABC','Item Name':'Item A','Buy':'B','Sell':'S','Inventory':'I','Inactive Item':'N','Standard Cost':'$12.50'}],
    }


def test_approval_requires_one_clean_batch_per_master_source():
    with Session(engine()) as s:
        for source, values in complete_values().items(): add_batch(s,source,values)
        actor=ensure_app_user(s,username='brad',display_name='Brad')
        summary=approve_master_batches(s,actor=actor)
        assert len(summary.approved_batch_ids)==3
        assert summary.accepted_row_count==3
        assert {r.status for r in s.scalars(select(ImportRow))}=={'accepted'}
        assert {b.status for b in s.scalars(select(ImportBatch))}=={'approved'}
        assert s.scalar(select(func.count(AuditEvent.audit_event_id)))==3


def test_approval_stops_when_any_issue_exists():
    with Session(engine()) as s:
        batches={}
        for source, values in complete_values().items(): batches[source]=add_batch(s,source,values)
        b=batches['item_master']
        s.add(ImportIssue(import_batch_id=b.import_batch_id,severity='error',issue_code='x',message='bad',resolution_status='open'))
        actor=ensure_app_user(s,username='brad',display_name='Brad')
        with pytest.raises(MasterImportError, match='review issue'):
            approve_master_batches(s,actor=actor)


def test_preview_then_commit_creates_masters_and_audit():
    with Session(engine()) as s:
        for source, values in complete_values().items(): add_batch(s,source,values,status='approved')
        preview=promote_master_batches(s,commit=False)
        assert preview.created==3 and preview.updated==0 and preview.unchanged==0
        assert s.scalar(select(func.count(Item.item_id)))==0
        actor=ensure_app_user(s,username='brad',display_name='Brad')
        committed=promote_master_batches(s,commit=True,actor=actor)
        assert committed.mode=='committed' and committed.created==3
        assert s.scalar(select(func.count(Item.item_id)))==1
        assert s.scalar(select(func.count(CustomerAccount.customer_account_id)))==1
        assert s.scalar(select(func.count(Supplier.supplier_id)))==1
        assert {b.status for b in s.scalars(select(ImportBatch))}=={'committed'}
        assert {r.status for r in s.scalars(select(ImportRow))}=={'committed'}
        assert s.scalar(select(func.count(AuditEvent.audit_event_id)))==6


def test_exact_updates_preserve_user_managed_fields():
    with Session(engine()) as s:
        for source, values in complete_values().items(): add_batch(s,source,values,status='approved')
        customer=CustomerAccount(myob_record_id='C1',myob_card_id='CUS1',display_name='Old',normalized_name='old',payment_basis='account',freight_payer='windsor',group_match_status='approved',is_active=True)
        supplier=Supplier(myob_record_id='S1',myob_card_id='SUP1',display_name='Old',normalized_name='old',default_manufacturing_lead_days=99,is_active=True)
        item=Item(item_number='ABC',item_name='Old',normalized_name='old',replenishment_policy='manual',policy_source='user',is_active=True)
        s.add_all([customer,supplier,item]); s.flush()
        actor=ensure_app_user(s,username='brad',display_name='Brad')
        summary=promote_master_batches(s,commit=True,actor=actor)
        assert summary.updated==3
        assert customer.display_name=='Customer A'
        assert customer.payment_basis=='account' and customer.freight_payer=='windsor' and customer.group_match_status=='approved'
        assert supplier.display_name=='Supplier A' and supplier.default_manufacturing_lead_days==99
        assert item.item_name=='Item A' and item.replenishment_policy=='manual' and item.policy_source=='user'


def test_card_id_collision_is_never_guessed():
    values=complete_values()
    values['customer_master']=[{'Co./Last Name':'Customer A','Record ID':'C1','Card ID':'COLLIDE','Card Status':'N'}]
    with Session(engine()) as s:
        for source, rows in values.items(): add_batch(s,source,rows,status='approved')
        s.add(CustomerAccount(myob_record_id='OTHER',myob_card_id='COLLIDE',display_name='Other',normalized_name='other',is_active=True))
        with pytest.raises(MasterImportError,match='different customer_account'):
            promote_master_batches(s,commit=False)
