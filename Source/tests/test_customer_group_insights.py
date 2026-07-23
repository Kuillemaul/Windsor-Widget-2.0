from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from windsor_widget.db.base import Base
from windsor_widget.db.models import CustomerAccount, CustomerGroup, ImportBatch, ImportRow, Item, SalesDocument, SalesLine
from windsor_widget.services.customer_group_insights import get_group_dashboard


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def test_group_dashboard_combines_state_accounts():
    with Session(_engine()) as session:
        group = CustomerGroup(display_name="Sealy of Australia", normalized_name="sealy of australia")
        session.add(group)
        session.flush()
        nsw = CustomerAccount(customer_group_id=group.customer_group_id, myob_record_id="NSW", display_name="Sealy NSW", normalized_name="sealy nsw", state="NSW", group_match_status="approved", is_active=True)
        vic = CustomerAccount(customer_group_id=group.customer_group_id, myob_record_id="VIC", display_name="Sealy VIC", normalized_name="sealy vic", state="Vic", group_match_status="approved", is_active=True)
        item = Item(item_number="I1", item_name="Item One", normalized_name="item one", is_active=True)
        batch = ImportBatch(source_type="sales_transactions", source_file_name="sales.TXT", file_sha256="s" * 64, status="committed", row_count=2, accepted_row_count=2, committed_at=datetime(2026, 7, 1))
        session.add_all([nsw, vic, item, batch])
        session.flush()

        for index, (account, qty) in enumerate(((nsw, Decimal("10")), (vic, Decimal("20"))), start=1):
            row = ImportRow(import_batch_id=batch.import_batch_id, row_number=index, raw_text="row", raw_json='{"values": {}}', natural_key=f"r{index}", row_sha256=f"r{index}".ljust(64, "0")[:64], status="committed", issue_count=0)
            session.add(row)
            session.flush()
            document = SalesDocument(customer_account_id=account.customer_account_id, myob_customer_record_id=account.myob_record_id, invoice_no=f"INV{index}", first_transaction_date=date(2026, 6, index), last_transaction_date=date(2026, 6, index), line_count=1, first_import_batch_id=batch.import_batch_id, last_import_batch_id=batch.import_batch_id)
            session.add(document)
            session.flush()
            session.add(SalesLine(sales_document_id=document.sales_document_id, item_id=item.item_id, line_sequence=1, source_import_row_id=row.import_row_id, source_row_sha256=row.row_sha256, last_import_batch_id=batch.import_batch_id, myob_item_number="I1", customer_name_snapshot=account.display_name, transaction_date=document.last_transaction_date, quantity=qty, unit_price=Decimal("2"), line_total=qty * Decimal("2"), freight_amount=Decimal("0"), sale_status="I", is_active=True))
        session.commit()

        dashboard = get_group_dashboard(session, group.customer_group_id, months=12, as_of_date=date(2026, 7, 31))

    assert dashboard.sales_period.quantity == Decimal("30")
    assert len(dashboard.accounts) == 2
    assert dashboard.items[0].account_count == 2
    assert dashboard.items[0].period_quantity == Decimal("30")
