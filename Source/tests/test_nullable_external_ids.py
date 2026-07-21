from __future__ import annotations

from sqlalchemy.dialects import mssql
from sqlalchemy.schema import CreateIndex

from windsor_widget.db.models.master_data import CustomerAccount, Supplier


def _index(table, name: str):
    return next(index for index in table.indexes if index.name == name)


def test_customer_external_id_indexes_are_filtered_for_sql_server() -> None:
    for name, column_name in (
        ("ux_customer_accounts_myob_record_id_not_null", "myob_record_id"),
        ("ux_customer_accounts_myob_card_id_not_null", "myob_card_id"),
    ):
        index = _index(CustomerAccount.__table__, name)
        ddl = str(CreateIndex(index).compile(dialect=mssql.dialect()))
        assert index.unique is True
        assert f"WHERE [{column_name}] IS NOT NULL" in ddl
        assert CustomerAccount.__table__.c[column_name].unique is not True


def test_supplier_external_id_indexes_are_filtered_for_sql_server() -> None:
    for name, column_name in (
        ("ux_suppliers_myob_record_id_not_null", "myob_record_id"),
        ("ux_suppliers_myob_card_id_not_null", "myob_card_id"),
    ):
        index = _index(Supplier.__table__, name)
        ddl = str(CreateIndex(index).compile(dialect=mssql.dialect()))
        assert index.unique is True
        assert f"WHERE [{column_name}] IS NOT NULL" in ddl
        assert Supplier.__table__.c[column_name].unique is not True
