from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from windsor_widget.db.base import Base
from windsor_widget.db.models import AppUser, AuditEvent, CustomerAccount, CustomerGroup, CustomerPriceFile
from windsor_widget.imports.normalization import normalize_name
from windsor_widget.services.customer_group_matching import apply_group_plan, build_group_plan


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def test_sealy_group_and_price_file(tmp_path: Path):
    source = tmp_path / "matching.xlsx"
    workbook = Workbook()
    customers = workbook.active
    customers.title = "Customer list Full"
    customers.append(["Co./Last Name", "", "City", "State", "", "Email"])
    names = (
        ("Sealy of Australia - Victoria", "Keysborough", "Vic"),
        ("Sealy of Australia - West Australia", "Canning Vale", "WA"),
        ("Sealy of Australia - N.S.W.", "Villawood", "NSW"),
        ("Sealy of Australia - South Australia", "Elizabeth", "S.A."),
        ("Sealy of Australia - Queensland", "Wacol", "QLD"),
    )
    for name, city, state in names:
        customers.append([name, "", city, state, "", "creditors3@sealy.com.au"])
    files = workbook.create_sheet("FILES")
    files.append([r"C:\Customer Prices\old\sealy of australia.xls"])
    files.append([r"C:\Customer Prices\sealy of australia.xlsx"])
    workbook.save(source)

    with Session(_engine()) as session:
        actor = AppUser(username="brad", display_name="Brad")
        session.add(actor)
        for name, city, state in names:
            session.add(
                CustomerAccount(
                    myob_record_id=name,
                    display_name=name,
                    normalized_name=normalize_name(name),
                    city=city,
                    state=state,
                    group_match_status="unmatched",
                    is_active=True,
                )
            )
        session.commit()

        plan = build_group_plan(session, source)
        sealy = next(p for p in plan.proposals if p.group_key == "sealy of australia")
        assert sealy.group_name == "Sealy of Australia"
        assert len(sealy.account_ids) == 5
        assert sealy.price_file_name == "sealy of australia.xlsx"
        assert sealy.price_confidence == 100

        summary = apply_group_plan(session, plan, actor_user_id=actor.user_id)
        session.commit()
        assert summary.groups_created == 1
        assert summary.accounts_assigned == 5
        assert summary.price_files_created == 1

        group = session.scalar(select(CustomerGroup))
        assert group.display_name == "Sealy of Australia"
        assert session.scalar(select(func.count(CustomerAccount.customer_account_id)).where(CustomerAccount.customer_group_id == group.customer_group_id)) == 5
        assert session.scalar(select(CustomerPriceFile)).file_name == "sealy of australia.xlsx"
        assert session.scalar(select(func.count(AuditEvent.audit_event_id))) == 7
