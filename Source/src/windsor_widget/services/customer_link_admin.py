"""Audited customer-group corrections and portable customer price-file paths."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from sqlalchemy import select, true
from sqlalchemy.orm import Session

from windsor_widget.db.models import (
    AuditEvent,
    CustomerAccount,
    CustomerGroup,
    CustomerPriceFile,
)
from windsor_widget.db.models.audit import utc_now
from windsor_widget.imports.normalization import normalize_name

_ALLOWED_EXTENSIONS = {".xlsx", ".xlsm", ".xls"}
_DEFAULT_SERVER_ROOTS = (
    Path(
        r"C:\Users\WindsorTradingInfo\WINDSOR TRADING CO TRUST"
        r"\Windsor Trading - Documents (1)\data\Customer Prices"
    ),
    Path(
        r"C:\Users\WindsorTradingInfo\WINDSOR TRADING CO TRUST"
        r"\Windsor Trading - Documents\data\Customer Prices"
    ),
)


@dataclass(frozen=True, slots=True)
class CustomerGroupChoice:
    customer_group_id: uuid.UUID
    display_name: str


@dataclass(frozen=True, slots=True)
class PriceFileChoice:
    relative_path: str
    file_name: str


@dataclass(frozen=True, slots=True)
class PriceScanResult:
    root: str | None
    files: tuple[PriceFileChoice, ...]
    warning: str | None


def price_file_relative_path(value: str) -> str:
    raw = str(value or "").strip().replace("/", "\\")
    if not raw:
        raise ValueError("A price-file path is required.")

    parts = list(PureWindowsPath(raw).parts)
    lower_parts = [part.casefold().rstrip("\\/") for part in parts]
    if "customer prices" in lower_parts:
        parts = parts[lower_parts.index("customer prices") + 1 :]
    elif PureWindowsPath(raw).is_absolute():
        raise ValueError(
            "The absolute path is not inside a Customer Prices folder."
        )

    clean = [part.strip("\\/") for part in parts if part not in {"", "\\", "/"}]
    if not clean or any(part in {".", ".."} for part in clean):
        raise ValueError("The relative price-file path is invalid.")

    relative = str(PureWindowsPath(*clean))
    if PureWindowsPath(relative).suffix.casefold() not in _ALLOWED_EXTENSIONS:
        raise ValueError("Price files must be .xlsx, .xlsm or .xls workbooks.")
    return relative


def resolve_server_price_root() -> Path | None:
    configured = os.getenv("WINDSOR_WIDGET_PRICE_FILE_ROOT", "").strip()
    candidates = (Path(configured),) if configured else _DEFAULT_SERVER_ROOTS
    return next((candidate for candidate in candidates if candidate.is_dir()), None)


def scan_server_price_files() -> PriceScanResult:
    scan_root = resolve_server_price_root()
    if scan_root is None:
        configured = os.getenv("WINDSOR_WIDGET_PRICE_FILE_ROOT", "").strip()
        warning = (
            f"Configured server price-file root does not exist: {configured}"
            if configured
            else (
                "No server Customer Prices folder was found. Set "
                "WINDSOR_WIDGET_PRICE_FILE_ROOT or enter a relative path manually."
            )
        )
        return PriceScanResult(None, (), warning)

    files: list[PriceFileChoice] = []
    seen: set[str] = set()
    for path in scan_root.rglob("*"):
        if not path.is_file() or path.name.startswith("~$"):
            continue
        if path.suffix.casefold() not in _ALLOWED_EXTENSIONS:
            continue
        relative_parts = path.relative_to(scan_root).parts
        if any(part.casefold() == "old" for part in relative_parts):
            continue
        relative = str(PureWindowsPath(*relative_parts))
        if relative.casefold() in seen:
            continue
        seen.add(relative.casefold())
        files.append(PriceFileChoice(relative, path.name))

    files.sort(key=lambda row: row.relative_path.casefold())
    return PriceScanResult(str(scan_root), tuple(files), None)


def list_active_customer_groups(session: Session) -> tuple[CustomerGroupChoice, ...]:
    return tuple(
        CustomerGroupChoice(group.customer_group_id, group.display_name)
        for group in session.scalars(
            select(CustomerGroup)
            .where(CustomerGroup.is_active == true())
            .order_by(CustomerGroup.display_name)
        )
    )


def set_customer_group_membership(
    session: Session,
    *,
    customer_account_id: uuid.UUID,
    selected_group_id: str,
    new_group_name: str,
    actor_user_id: uuid.UUID,
) -> CustomerAccount:
    account = session.get(CustomerAccount, customer_account_id)
    if account is None:
        raise LookupError("Customer not found.")

    old_group = (
        session.get(CustomerGroup, account.customer_group_id)
        if account.customer_group_id is not None
        else None
    )

    requested_name = new_group_name.strip()
    selected = selected_group_id.strip()
    target: CustomerGroup | None = None

    if requested_name:
        normalized = normalize_name(requested_name)
        if not normalized:
            raise ValueError("The new group name is invalid.")
        target = session.scalar(
            select(CustomerGroup).where(CustomerGroup.normalized_name == normalized)
        )
        if target is None:
            target = CustomerGroup(
                display_name=requested_name,
                normalized_name=normalized,
                is_active=True,
                notes="Created from Customer Summary edit mode.",
            )
            session.add(target)
            session.flush()
            session.add(
                AuditEvent(
                    actor_user_id=actor_user_id,
                    action="customer.group.created",
                    entity_type="customer_group",
                    entity_id=str(target.customer_group_id),
                    source="web",
                    summary=f"Created customer group {target.display_name}.",
                    after_json=json.dumps(
                        {
                            "display_name": target.display_name,
                            "normalized_name": target.normalized_name,
                        },
                        sort_keys=True,
                    ),
                )
            )
    elif selected:
        try:
            target = session.get(CustomerGroup, uuid.UUID(selected))
        except ValueError as exc:
            raise ValueError("The selected group is invalid.") from exc
        if target is None or not target.is_active:
            raise LookupError("The selected group does not exist.")

    before = {
        "customer_group_id": str(account.customer_group_id) if account.customer_group_id else None,
        "group_name": old_group.display_name if old_group else None,
        "group_match_status": account.group_match_status,
    }
    account.customer_group_id = target.customer_group_id if target else None
    account.group_match_status = "approved" if target else "unmatched"
    after = {
        "customer_group_id": str(account.customer_group_id) if account.customer_group_id else None,
        "group_name": target.display_name if target else None,
        "group_match_status": account.group_match_status,
    }

    if before != after:
        session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                action="customer.group.corrected",
                entity_type="customer_account",
                entity_id=str(account.customer_account_id),
                source="web",
                summary=(
                    f"{account.display_name} moved to {target.display_name}."
                    if target
                    else f"{account.display_name} removed from its group."
                ),
                before_json=json.dumps(before, sort_keys=True),
                after_json=json.dumps(after, sort_keys=True),
            )
        )
    session.flush()
    return account


def update_customer_group(
    session: Session,
    *,
    customer_group_id: uuid.UUID,
    display_name: str,
    relative_price_path: str,
    unlink_price_file: bool,
    actor_user_id: uuid.UUID,
) -> CustomerGroup:
    group = session.get(CustomerGroup, customer_group_id)
    if group is None:
        raise LookupError("Customer group not found.")

    requested_name = display_name.strip()
    if not requested_name:
        raise ValueError("A customer group name is required.")
    normalized = normalize_name(requested_name)
    duplicate = session.scalar(
        select(CustomerGroup).where(
            CustomerGroup.normalized_name == normalized,
            CustomerGroup.customer_group_id != customer_group_id,
        )
    )
    if duplicate is not None:
        raise ValueError(
            f"Another group already uses the name {duplicate.display_name!r}."
        )

    current_files = tuple(
        session.scalars(
            select(CustomerPriceFile).where(
                CustomerPriceFile.customer_group_id == customer_group_id,
                CustomerPriceFile.is_active == true(),
            )
        )
    )
    before = {
        "display_name": group.display_name,
        "active_price_files": [row.file_path for row in current_files],
    }

    group.display_name = requested_name
    group.normalized_name = normalized

    if unlink_price_file:
        for row in current_files:
            row.is_active = False
    elif relative_price_path.strip():
        relative = price_file_relative_path(relative_price_path)
        for row in current_files:
            row.is_active = False
        chosen = session.scalar(
            select(CustomerPriceFile).where(
                CustomerPriceFile.customer_group_id == customer_group_id,
                CustomerPriceFile.file_path == relative,
            )
        )
        if chosen is None:
            chosen = CustomerPriceFile(
                customer_group_id=customer_group_id,
                file_path=relative,
                file_name=PureWindowsPath(relative).name,
                match_status="approved",
                confidence=100,
                is_active=True,
                verified_at=utc_now(),
                verified_by_user_id=actor_user_id,
            )
            session.add(chosen)
        else:
            chosen.file_name = PureWindowsPath(relative).name
            chosen.match_status = "approved"
            chosen.confidence = 100
            chosen.is_active = True
            chosen.verified_at = utc_now()
            chosen.verified_by_user_id = actor_user_id

    session.flush()
    active_after = tuple(
        session.scalars(
            select(CustomerPriceFile).where(
                CustomerPriceFile.customer_group_id == customer_group_id,
                CustomerPriceFile.is_active == true(),
            )
        )
    )
    after = {
        "display_name": group.display_name,
        "active_price_files": [row.file_path for row in active_after],
    }
    if before != after:
        session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                action="customer.group.settings.corrected",
                entity_type="customer_group",
                entity_id=str(group.customer_group_id),
                source="web",
                summary=f"Updated customer group {group.display_name}.",
                before_json=json.dumps(before, sort_keys=True),
                after_json=json.dumps(after, sort_keys=True),
            )
        )
    session.flush()
    return group


def normalize_existing_price_file_paths(
    session: Session,
    *,
    actor_user_id: uuid.UUID,
) -> tuple[int, int]:
    converted = 0
    skipped = 0
    for price_file in session.scalars(select(CustomerPriceFile)):
        try:
            relative = price_file_relative_path(price_file.file_path)
        except ValueError:
            skipped += 1
            continue
        if relative == price_file.file_path:
            continue

        collision = session.scalar(
            select(CustomerPriceFile).where(
                CustomerPriceFile.customer_group_id == price_file.customer_group_id,
                CustomerPriceFile.file_path == relative,
                CustomerPriceFile.customer_price_file_id
                != price_file.customer_price_file_id,
            )
        )
        if collision is not None:
            collision.is_active = collision.is_active or price_file.is_active
            price_file.is_active = False
            skipped += 1
            continue

        old_path = price_file.file_path
        price_file.file_path = relative
        price_file.file_name = PureWindowsPath(relative).name
        converted += 1
        session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                action="customer.price_file.path.normalized",
                entity_type="customer_price_file",
                entity_id=str(price_file.customer_price_file_id),
                source="maintenance",
                summary=f"Normalised price-file path to {relative}.",
                before_json=json.dumps({"file_path": old_path}, sort_keys=True),
                after_json=json.dumps({"file_path": relative}, sort_keys=True),
            )
        )
    session.flush()
    return converted, skipped
