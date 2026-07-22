"""Administrative commands for web user accounts."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from sqlalchemy import select

from windsor_widget.config import load_settings
from windsor_widget.db.models import AppUser, WebUserAccount
from windsor_widget.db.session import create_database_engine, create_session_factory
from windsor_widget.web.auth import upsert_web_user


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Windsor Widget web accounts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create-user")
    create.add_argument("--config", type=Path, default=Path("config/development.local.json"))
    create.add_argument("--username", required=True)
    create.add_argument("--display-name", required=True)
    create.add_argument("--email")
    create.add_argument(
        "--role",
        choices=("admin", "procurement", "read_only"),
        default="read_only",
    )

    listing = subparsers.add_parser("list-users")
    listing.add_argument("--config", type=Path, default=Path("config/development.local.json"))

    args = parser.parse_args()
    settings = load_settings(args.config)
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        with factory() as session:
            if args.command == "create-user":
                password = os.environ.get("WINDSOR_WIDGET_INITIAL_PASSWORD", "")
                if not password:
                    raise RuntimeError(
                        "WINDSOR_WIDGET_INITIAL_PASSWORD is required. "
                        "Use scripts\\web_workflow.ps1 -Action CreateAdmin."
                    )
                principal = upsert_web_user(
                    session,
                    username=args.username,
                    display_name=args.display_name,
                    password=password,
                    role=args.role,
                    email=args.email,
                )
                session.commit()
                print(
                    f"Web user ready: {principal.username} | "
                    f"{principal.display_name} | role={principal.role}"
                )
                return 0

            rows = session.execute(
                select(AppUser, WebUserAccount)
                .join(WebUserAccount, WebUserAccount.user_id == AppUser.user_id)
                .order_by(AppUser.username)
            )
            print("Username\tDisplay Name\tRole\tActive\tLast Login")
            for user, account in rows:
                print(
                    f"{user.username}\t{user.display_name}\t{account.role}\t"
                    f"{user.is_active}\t{account.last_login_at or '-'}"
                )
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
