from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from windsor_widget.db.base import Base
from windsor_widget.db.models import (
    AppUser,
    AuditEvent,
    CustomerAccount,
    CustomerGroup,
    CustomerPriceFile,
)
from windsor_widget.services.customer_link_admin import (
    normalize_existing_price_file_paths,
    price_file_relative_path,
    set_customer_group_membership,
    update_customer_group,
)


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def test_full_onedrive_path_becomes_portable_relative_path():
    value = (
        r"C:\Users\WindsorTradingInfo\WINDSOR TRADING CO TRUST"
        r"\Windsor Trading - Documents (1)\data\Customer Prices"
        r"\Sealy\sealy of australia.xlsx"
    )
    assert price_file_relative_path(value) == r"Sealy\sealy of australia.xlsx"


def test_edit_services_move_account_and_correct_group_file():
    with Session(_engine()) as session:
        actor = AppUser(username="brad", display_name="Brad")
        old_group = CustomerGroup(display_name="Old", normalized_name="old")
        sealy = CustomerGroup(display_name="Sealy", normalized_name="sealy")
        account = CustomerAccount(
            group=old_group,
            myob_record_id="C1",
            display_name="Sealy NSW",
            normalized_name="sealy nsw",
            group_match_status="approved",
            is_active=True,
        )
        old_file = CustomerPriceFile(
            group=sealy,
            file_path=r"C:\Temp\Customer Prices\old\sealy.xls",
            file_name="sealy.xls",
            match_status="approved",
            is_active=True,
        )
        session.add_all([actor, old_group, sealy, account, old_file])
        session.commit()

        set_customer_group_membership(
            session,
            customer_account_id=account.customer_account_id,
            selected_group_id=str(sealy.customer_group_id),
            new_group_name="",
            actor_user_id=actor.user_id,
        )
        update_customer_group(
            session,
            customer_group_id=sealy.customer_group_id,
            display_name="Sealy of Australia",
            relative_price_path="sealy of australia.xlsx",
            unlink_price_file=False,
            actor_user_id=actor.user_id,
        )
        session.commit()
        session.refresh(account)
        session.refresh(old_file)

        assert account.customer_group_id == sealy.customer_group_id
        assert old_file.is_active is False
        active = session.scalar(
            select(CustomerPriceFile).where(CustomerPriceFile.is_active == True)
        )
        assert active.file_path == "sealy of australia.xlsx"
        assert session.scalar(select(func.count(AuditEvent.audit_event_id))) == 2


def test_normalise_existing_paths():
    with Session(_engine()) as session:
        actor = AppUser(username="brad", display_name="Brad")
        group = CustomerGroup(display_name="Sealy", normalized_name="sealy")
        price_file = CustomerPriceFile(
            group=group,
            file_path=r"C:\Temp\Customer Prices\sealy of australia.xlsx",
            file_name="sealy of australia.xlsx",
            match_status="approved",
            is_active=True,
        )
        session.add_all([actor, group, price_file])
        session.commit()

        converted, skipped = normalize_existing_price_file_paths(
            session,
            actor_user_id=actor.user_id,
        )
        session.commit()
        session.refresh(price_file)

        assert converted == 1
        assert skipped == 0
        assert price_file.file_path == "sealy of australia.xlsx"
