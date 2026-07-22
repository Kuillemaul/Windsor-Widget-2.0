from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from windsor_widget.db.base import Base
from windsor_widget.db.models import AppUser, WebUserAccount
from windsor_widget.web.auth import AuthenticationError, hash_password, verify_password


WEB_ROOT = Path(__file__).parents[1] / "src" / "windsor_widget" / "web"


def test_web_account_table_is_registered() -> None:
    assert "web_user_accounts" in Base.metadata.tables
    table = Base.metadata.tables["web_user_accounts"]
    assert table.c.user_id.primary_key is True
    assert table.c.password_hash.nullable is False
    assert table.c.role.nullable is False


def test_web_schema_can_be_created_in_memory() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    assert "web_user_accounts" in inspect(engine).get_table_names()
    with Session(engine) as session:
        user = AppUser(username="brad", display_name="Brad Mayze", is_active=True)
        session.add(user)
        session.flush()
        session.add(
            WebUserAccount(
                user_id=user.user_id,
                password_hash=hash_password("correct horse battery staple"),
                role="admin",
            )
        )
        session.commit()
        assert session.get(WebUserAccount, user.user_id).role == "admin"


def test_argon2_password_hashing_and_minimum_length() -> None:
    encoded = hash_password("correct horse battery staple")
    verified, replacement = verify_password(encoded, "correct horse battery staple")
    assert verified is True
    assert replacement is None
    assert verify_password(encoded, "incorrect password")[0] is False
    with pytest.raises(AuthenticationError):
        hash_password("too-short")


def test_all_three_themes_and_logo_are_packaged() -> None:
    css = (WEB_ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
    javascript = (WEB_ROOT / "static" / "js" / "theme.js").read_text(encoding="utf-8")
    for theme in ("windsor", "light", "dark"):
        assert f'data-theme="{theme}"' in css or theme == "windsor"
        assert f'"{theme}"' in javascript
    assert (WEB_ROOT / "static" / "img" / "windsor-logo.jpg").stat().st_size > 10_000


def test_templates_do_not_use_external_cdn_assets() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in (WEB_ROOT / "templates").glob("*.html")
    )
    assert "https://" not in combined
    assert "http://" not in combined



def test_item_summary_assets_and_drilldown_links_are_packaged() -> None:
    templates = WEB_ROOT / "templates"
    detail = (templates / "item_detail.html").read_text(encoding="utf-8")
    items = (templates / "items.html").read_text(encoding="utf-8")
    order_analysis = (templates / "order_analysis.html").read_text(encoding="utf-8")
    assert "Explicit Customer Cover" in detail
    assert "Suggested Order" in detail
    assert "Monthly invoiced sales" in detail
    assert "item_detail" in items
    assert "item_detail" in order_analysis
    assert (WEB_ROOT / "static" / "css" / "item-detail.css").is_file()
