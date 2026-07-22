from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from windsor_widget.db.base import Base
from windsor_widget.db.models import (
    AppUser, CoverOrderDocument, CoverOrderLine, CoverOrderSnapshot,
    CustomerAccount, ImportBatch, ImportRow, InventorySnapshot,
    InventorySnapshotLine, Item, PurchaseDocument, PurchaseLine,
    SalesDocument, SalesLine, Supplier,
)
from windsor_widget.services.item_policy import list_item_policy_rows, set_item_policy


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _batch(session: Session, source_type: str, marker: str, count: int):
    batch = ImportBatch(source_type=source_type, source_file_name=f"{marker}.txt", file_sha256=marker.ljust(64,"0")[:64], status="committed", row_count=count, accepted_row_count=count, committed_at=datetime(2026,7,22))
    session.add(batch); session.flush(); rows=[]
    for index in range(1,count+1):
        row=ImportRow(import_batch_id=batch.import_batch_id,row_number=index,raw_text="row",raw_json='{"values": {}}',natural_key=f"{marker}-{index}",row_sha256=f"{marker}-{index}".ljust(64,"0")[:64],status="committed",issue_count=0)
        session.add(row); rows.append(row)
    session.flush(); return batch,rows


def _seed(session: Session):
    actor=AppUser(username="brad",display_name="Brad")
    customer=CustomerAccount(myob_record_id="C1",display_name="Customer",normalized_name="customer",is_active=True)
    supplier=Supplier(myob_record_id="S1",display_name="Supplier",normalized_name="supplier",is_active=True)
    item=Item(item_number="CARPET-TAPE",item_name="Carpet Tape",normalized_name="carpet tape",is_active=True,is_inventoried=True,is_bought=True,is_sold=True,replenishment_policy="unknown")
    session.add_all([actor,customer,supplier,item]); session.flush()
    inv=InventorySnapshot(captured_at=datetime(2026,7,22),source_file_name="inventory.xlsx",source_sha256="i"*64,row_count=1,is_current=True,committed_by_user_id=actor.user_id)
    session.add(inv); session.flush(); session.add(InventorySnapshotLine(inventory_snapshot_id=inv.inventory_snapshot_id,item_id=item.item_id,source_row_number=1,item_number_snapshot=item.item_number,item_name_snapshot=item.item_name,on_hand=Decimal("0"),committed=Decimal("0"),on_order=Decimal("0"),available=Decimal("0")))
    sb,sr=_batch(session,"sales_transactions","sales",3); pb,pr=_batch(session,"purchase_transactions","purchase",3)
    pairs=[(date(2026,1,1),date(2026,1,4)),(date(2026,3,1),date(2026,3,3)),(date(2026,5,1),date(2026,5,5))]
    for index,(sd,pd) in enumerate(pairs,1):
        sdoc=SalesDocument(customer_account_id=customer.customer_account_id,myob_customer_record_id="C1",invoice_no=f"INV{index}",first_transaction_date=sd,last_transaction_date=sd,line_count=1,first_import_batch_id=sb.import_batch_id,last_import_batch_id=sb.import_batch_id)
        pdoc=PurchaseDocument(supplier_id=supplier.supplier_id,myob_supplier_record_id="S1",purchase_no=f"BILL{index}",first_transaction_date=pd,last_transaction_date=pd,line_count=1,first_import_batch_id=pb.import_batch_id,last_import_batch_id=pb.import_batch_id)
        session.add_all([sdoc,pdoc]); session.flush()
        session.add(SalesLine(sales_document_id=sdoc.sales_document_id,item_id=item.item_id,line_sequence=1,source_import_row_id=sr[index-1].import_row_id,source_row_sha256=sr[index-1].row_sha256,last_import_batch_id=sb.import_batch_id,myob_item_number=item.item_number,customer_name_snapshot="Customer",transaction_date=sd,quantity=Decimal("100"),unit_price=Decimal("2"),line_total=Decimal("200"),sale_status="I",is_cover_order=False,is_active=True))
        session.add(PurchaseLine(purchase_document_id=pdoc.purchase_document_id,item_id=item.item_id,line_sequence=1,source_import_row_id=pr[index-1].import_row_id,source_row_sha256=pr[index-1].row_sha256,last_import_batch_id=pb.import_batch_id,myob_item_number=item.item_number,supplier_name_snapshot="Supplier",transaction_date=pd,quantity=Decimal("100"),unit_price=Decimal("1"),line_total=Decimal("100"),is_active=True))
    cb,cr=_batch(session,"cover_order_snapshot","cover",2)
    snap=CoverOrderSnapshot(import_batch_id=cb.import_batch_id,captured_at=datetime(2026,7,22),source_file_name="orders.txt",document_count=1,row_count=2,is_current=True,committed_by_user_id=actor.user_id)
    session.add(snap); session.flush(); doc=CoverOrderDocument(cover_order_snapshot_id=snap.cover_order_snapshot_id,customer_account_id=customer.customer_account_id,myob_customer_record_id="C1",invoice_no="26001264",first_transaction_date=date(2026,7,1),last_transaction_date=date(2026,7,1),line_count=2)
    session.add(doc); session.flush()
    session.add_all([
        CoverOrderLine(cover_order_document_id=doc.cover_order_document_id,item_id=item.item_id,line_sequence=1,source_import_row_id=cr[0].import_row_id,source_row_sha256=cr[0].row_sha256,myob_item_number=item.item_number,customer_name_snapshot="Customer",transaction_date=date(2026,7,1),quantity=Decimal("50"),unit_price=Decimal("2"),line_total=Decimal("100"),is_cover_order=True),
        CoverOrderLine(cover_order_document_id=doc.cover_order_document_id,item_id=item.item_id,line_sequence=2,source_import_row_id=cr[1].import_row_id,source_row_sha256=cr[1].row_sha256,myob_item_number=item.item_number,customer_name_snapshot="Customer",transaction_date=date(2026,7,1),quantity=Decimal("999"),unit_price=Decimal("2"),line_total=Decimal("1998"),is_cover_order=False),
    ]); session.commit(); return actor,item


def test_cover_tag_and_review_candidate_are_conservative():
    with Session(_engine()) as session:
        _,item=_seed(session); row=list_item_policy_rows(session,query="CARPET",limit=20)[0]
        assert row.outstanding_cover==Decimal("50")
        assert "COVER" in row.tags and "REVIEW" in row.tags
        assert row.review_confidence=="High" and row.matched_cycles==3


def test_policy_approval_replaces_review_with_mto():
    with Session(_engine()) as session:
        actor,item=_seed(session); set_item_policy(session,item_id=item.item_id,policy="make_to_order",actor_user_id=actor.user_id); session.commit()
        row=list_item_policy_rows(session,query="CARPET",limit=20)[0]
        assert row.replenishment_policy=="make_to_order"
        assert "MTO" in row.tags and "REVIEW" not in row.tags
