from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from windsor_widget.db.base import Base
from windsor_widget.db.models import (
    AppUser,
    AuditEvent,
    CustomerAccount,
    ImportBatch,
    ImportRow,
    SalesDocument,
    SalesLine,
)
from windsor_widget.services.customer_insights import (
    get_customer_invoice_detail,
    get_customer_invoices,
)
from windsor_widget.services.freight_inference import (
    apply_customer_freight_inference,
    get_customer_freight_evidence,
)


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed(session: Session):
    actor = AppUser(username="brad", display_name="Brad")
    customers = [
        CustomerAccount(
            myob_record_id=f"C{index}",
            display_name=name,
            normalized_name=name.casefold(),
            freight_payer=freight_payer,
            is_active=True,
        )
        for index, (name, freight_payer) in enumerate(
            (
                ("Customer Majority Charged", "unknown"),
                ("Customer Majority Zero", "unknown"),
                ("Customer Even Split", "unknown"),
                ("Manual Freight Customer", "customer"),
            ),
            start=1,
        )
    ]
    batch = ImportBatch(
        source_type="sales_transactions",
        source_file_name="sales.TXT",
        file_sha256="f" * 64,
        status="committed",
        row_count=13,
        accepted_row_count=13,
        committed_at=datetime(2026, 7, 1),
    )
    session.add_all([actor, *customers, batch])
    session.flush()

    row_number = 0
    created_documents = []
    invoice_sets = (
        (customers[0], (Decimal("15"), Decimal("0"), Decimal("20"))),
        (customers[1], (Decimal("0"), Decimal("0"), Decimal("0"))),
        (customers[2], (Decimal("10"), Decimal("0"))),
        (customers[3], (Decimal("0"), Decimal("0"))),
    )
    for customer, freight_values in invoice_sets:
        for invoice_index, freight in enumerate(freight_values, start=1):
            document = SalesDocument(
                customer_account_id=customer.customer_account_id,
                myob_customer_record_id=customer.myob_record_id,
                invoice_no=f"{customer.myob_record_id}-INV-{invoice_index}",
                first_transaction_date=date(2026, invoice_index, 1),
                last_transaction_date=date(2026, invoice_index, 1),
                line_count=2 if freight > 0 else 1,
                first_import_batch_id=batch.import_batch_id,
                last_import_batch_id=batch.import_batch_id,
            )
            session.add(document)
            session.flush()
            created_documents.append(document)

            line_count = 2 if freight > 0 else 1
            for line_sequence in range(1, line_count + 1):
                row_number += 1
                source_row = ImportRow(
                    import_batch_id=batch.import_batch_id,
                    row_number=row_number,
                    raw_text="row",
                    raw_json='{"values": {}}',
                    natural_key=f"row-{row_number}",
                    row_sha256=f"row-{row_number}".ljust(64, "0")[:64],
                    status="committed",
                    issue_count=0,
                )
                session.add(source_row)
                session.flush()
                session.add(
                    SalesLine(
                        sales_document_id=document.sales_document_id,
                        item_id=None,
                        line_sequence=line_sequence,
                        source_import_row_id=source_row.import_row_id,
                        source_row_sha256=source_row.row_sha256,
                        last_import_batch_id=batch.import_batch_id,
                        customer_name_snapshot=customer.display_name,
                        transaction_date=document.last_transaction_date,
                        description="Freight test",
                        quantity=Decimal("1"),
                        unit_price=Decimal("10"),
                        line_total=Decimal("10"),
                        freight_amount=freight,
                        sale_status="I",
                        is_active=True,
                    )
                )
    session.commit()
    return actor, customers, created_documents


def test_invoice_freight_is_deduplicated_and_majority_inferred():
    with Session(_engine()) as session:
        actor, customers, _documents = _seed(session)
        evidence = get_customer_freight_evidence(
            session,
            as_of_date=date(2026, 12, 31),
        )

        charged = evidence[customers[0].customer_account_id]
        assert charged.invoice_count == 3
        assert charged.charged_invoice_count == 2
        assert charged.zero_invoice_count == 1
        assert charged.total_invoice_freight == Decimal("35")
        assert charged.suggested_payer == "customer"

        zero = evidence[customers[1].customer_account_id]
        assert zero.invoice_count == 3
        assert zero.suggested_payer == "windsor"

        split = evidence[customers[2].customer_account_id]
        assert split.invoice_count == 2
        assert split.suggested_payer == "unknown"

        summary = apply_customer_freight_inference(
            session,
            evidence,
            actor_user_id=actor.user_id,
        )
        session.commit()
        assert summary.applied_customer == 1
        assert summary.applied_windsor == 1
        assert summary.skipped_existing == 1

        for customer in customers:
            session.refresh(customer)
        assert customers[0].freight_payer == "customer"
        assert customers[1].freight_payer == "windsor"
        assert customers[2].freight_payer == "unknown"
        assert customers[3].freight_payer == "customer"

        audit_count = session.scalar(
            select(func.count(AuditEvent.audit_event_id)).where(
                AuditEvent.action == "customer.freight_payer.inferred"
            )
        )
        assert audit_count == 2


def test_customer_invoice_views_show_one_invoice_freight_amount():
    with Session(_engine()) as session:
        _actor, customers, documents = _seed(session)
        invoices = get_customer_invoices(
            session,
            customers[0].customer_account_id,
            as_of_date=date(2026, 12, 31),
        )
        invoice_1 = next(row for row in invoices if row.invoice_no == "C1-INV-1")
        assert invoice_1.freight_amount == Decimal("15")

        detail = get_customer_invoice_detail(
            session,
            customers[0].customer_account_id,
            documents[0].sales_document_id,
        )
        assert detail.freight_amount == Decimal("15")
