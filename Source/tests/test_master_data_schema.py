from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from windsor_widget.db.base import Base
from windsor_widget.db.models import CustomerAccount, CustomerGroup, Item


def test_customer_group_can_contain_several_myob_accounts() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    group = CustomerGroup(display_name="Example Group", normalized_name="example group")
    group.accounts.extend(
        [
            CustomerAccount(
                display_name="Example Victoria",
                normalized_name="example victoria",
                myob_record_id="1001",
            ),
            CustomerAccount(
                display_name="Example NSW",
                normalized_name="example nsw",
                myob_record_id="1002",
            ),
        ]
    )
    with Session(engine) as session:
        session.add(group)
        session.commit()

        assert len(group.accounts) == 2
        assert {account.myob_record_id for account in group.accounts} == {"1001", "1002"}


def test_control_item_can_be_retained_but_excluded_from_planning_view() -> None:
    item = Item(
        item_number="\\FC",
        item_name="Freight charge",
        normalized_name="freight charge",
        excluded_from_item_view=True,
    )

    assert item.item_number == "\\FC"
    assert item.excluded_from_item_view is True
