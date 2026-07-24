"""Read-only extraction of supplier packing facts from the Yuchang order workbook."""

from __future__ import annotations

import math
import re
import zipfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from openpyxl.utils import column_index_from_string

SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
ET_NS = {"s": SHEET_NS, "r": OFFICE_REL_NS, "rel": PACKAGE_REL_NS}
ZERO = Decimal("0")
RATIO_TOLERANCE = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class YuchangPackingSourceRow:
    source_row: int
    item_number: str
    supplier_description: str
    size: str
    colour: str
    supplier_unit: str
    quantity_per_supplier_unit_raw: str
    label_description: str
    quantity_per_carton_raw: str
    quantity_per_pallet_raw: str
    number_of_pallets_raw: str
    fob_price_raw: str


@dataclass(frozen=True, slots=True)
class YuchangPackingPreviewRow:
    source_row: int
    item_number: str
    item_number_key: str
    workbook_mapping_count: int
    widget_match_status: str
    widget_item_number: str
    widget_item_name: str
    supplier_link_status: str
    supplier_item_number: str
    supplier_description_raw: str
    size_raw: str
    colour_raw: str
    supplier_unit_raw: str
    quantity_per_supplier_unit_raw: str
    label_description_raw: str
    quantity_per_carton_raw: str
    quantity_per_pallet_raw: str
    fob_price_raw: str
    inferred_measure: str
    parsed_quantity_per_supplier_unit: str
    parsed_quantity_per_carton: str
    parsed_supplier_units_per_carton: str
    parsed_roll_or_spool_length_metres: str
    parsed_metres_per_carton: str
    parsed_quantity_per_pallet: str
    parsed_cartons_per_pallet: str
    parsed_fob_price: str
    preview_status: str
    review_reason: str
    proposed_action: str

    def as_csv_dict(self) -> dict[str, object]:
        return asdict(self)


def clean_item_key(value: object) -> str:
    return re.sub(r"[\s\u00A0]+", "", str(value or "").strip()).upper()


def _clean_text(value: object) -> str:
    return re.sub(r"[\s\u00A0]+", " ", str(value or "").strip())


def _decimal(value: object) -> Decimal | None:
    text = str(value or "").strip().replace(",", "").replace("$", "")
    if not text:
        return None
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    if not result.is_finite():
        return None
    return result


def _decimal_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _near_integer(value: Decimal) -> bool:
    nearest = value.to_integral_value()
    return abs(value - nearest) <= RATIO_TOLERANCE


def infer_measure(supplier_unit: str) -> str:
    unit = _clean_text(supplier_unit).casefold()
    if any(token in unit for token in ("roll", "spool", "reel", "coil")):
        return "metre"
    if any(token in unit for token in ("piece", "pcs", "pc", "pair", "set")):
        return "piece"
    if any(token in unit for token in ("kg", "kilo")):
        return "kilogram"
    return "unknown"


def _shared_strings(zip_file: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zip_file.namelist():
        return []
    root = ET.fromstring(zip_file.read("xl/sharedStrings.xml"))
    return [
        "".join(node.text or "" for node in item.iter(f"{{{SHEET_NS}}}t"))
        for item in root.findall("s:si", ET_NS)
    ]


def _cell_text(cell: Any, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{{{SHEET_NS}}}t"))
    value_node = cell.find("s:v", ET_NS)
    if value_node is None or value_node.text is None:
        return ""
    value = value_node.text
    if cell_type == "s":
        try:
            return shared_strings[int(value)]
        except (IndexError, ValueError):
            return value
    return value


def _sheet_part(zip_file: zipfile.ZipFile, sheet_name: str) -> str:
    workbook_root = ET.fromstring(zip_file.read("xl/workbook.xml"))
    rels_root = ET.fromstring(zip_file.read("xl/_rels/workbook.xml.rels"))
    targets = {rel.attrib.get("Id"): rel.attrib.get("Target", "") for rel in rels_root}
    sheets = workbook_root.find("s:sheets", ET_NS)
    if sheets is None:
        raise ValueError("Workbook contains no worksheets.")
    for sheet in sheets.findall("s:sheet", ET_NS):
        if sheet.attrib.get("name") != sheet_name:
            continue
        rel_id = sheet.attrib.get(f"{{{OFFICE_REL_NS}}}id")
        target = targets.get(rel_id, "")
        if not target:
            break
        if target.startswith("/"):
            return target.lstrip("/")
        return str(PurePosixPath("xl") / target)
    available = [sheet.attrib.get("name", "") for sheet in sheets.findall("s:sheet", ET_NS)]
    raise ValueError(
        f"Worksheet {sheet_name!r} was not found. Available sheets: {', '.join(available)}"
    )


def _column_number(cell_reference: str) -> int:
    match = re.match(r"^([A-Z]+)", str(cell_reference or "").upper())
    if not match:
        return 0
    return column_index_from_string(match.group(1))


def _is_detail_row(values: dict[int, str]) -> bool:
    description = _clean_text(values.get(2, ""))
    if not description:
        return False
    return any(_clean_text(values.get(column, "")) for column in range(3, 9))


def extract_yuchang_packing_rows(
    workbook_path: str | Path,
    *,
    worksheet_name: str = "Sheet1",
    header_end_row: int = 14,
) -> tuple[YuchangPackingSourceRow, ...]:
    """Read Yuchang detail rows without changing or recalculating the workbook."""

    path = Path(workbook_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"YU workbook was not found: {path}")

    with zipfile.ZipFile(path, "r") as zip_file:
        shared_strings = _shared_strings(zip_file)
        part = _sheet_part(zip_file, worksheet_name)
        root = ET.fromstring(zip_file.read(part))
        sheet_data = root.find("s:sheetData", ET_NS)
        if sheet_data is None:
            raise ValueError(f"Worksheet {worksheet_name!r} contains no row data.")

        rows: list[YuchangPackingSourceRow] = []
        for row in sheet_data.findall("s:row", ET_NS):
            row_number = int(row.attrib.get("r", "0") or 0)
            if row_number <= header_end_row:
                continue
            values = {
                _column_number(cell.attrib.get("r", "")): _cell_text(cell, shared_strings)
                for cell in row.findall("s:c", ET_NS)
            }
            normalized_values = {
                column: _clean_text(value) for column, value in values.items()
            }
            export_text = " ".join(
                normalized_values.get(column, "").upper() for column in range(1, 14)
            )
            if "METHOD OF TRANSPORT" in export_text or "ORDER TOTAL" in export_text:
                break
            if not _is_detail_row(normalized_values):
                continue
            rows.append(
                YuchangPackingSourceRow(
                    source_row=row_number,
                    item_number=normalized_values.get(1, ""),
                    supplier_description=normalized_values.get(2, ""),
                    size=normalized_values.get(3, ""),
                    colour=normalized_values.get(4, ""),
                    supplier_unit=normalized_values.get(5, ""),
                    quantity_per_supplier_unit_raw=normalized_values.get(6, ""),
                    label_description=normalized_values.get(7, ""),
                    quantity_per_carton_raw=normalized_values.get(8, ""),
                    quantity_per_pallet_raw=normalized_values.get(9, ""),
                    number_of_pallets_raw=normalized_values.get(10, ""),
                    fob_price_raw=normalized_values.get(11, ""),
                )
            )
    return tuple(rows)


def workbook_mapping_counts(rows: Iterable[YuchangPackingSourceRow]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        key = clean_item_key(row.item_number)
        if key:
            counts[key] += 1
    return dict(counts)


def build_yuchang_packing_preview_row(
    source: YuchangPackingSourceRow,
    *,
    mapping_count: int,
    widget_matches: Iterable[dict[str, object]],
    supplier_link: dict[str, object] | None = None,
) -> YuchangPackingPreviewRow:
    matches = list(widget_matches)
    widget_match_status = "matched" if len(matches) == 1 else "missing"
    if len(matches) > 1:
        widget_match_status = "collision"
    matched = matches[0] if len(matches) == 1 else {}
    link = supplier_link or {}

    quantity_per_unit = _decimal(source.quantity_per_supplier_unit_raw)
    quantity_per_carton = _decimal(source.quantity_per_carton_raw)
    quantity_per_pallet = _decimal(source.quantity_per_pallet_raw)
    fob_price = _decimal(source.fob_price_raw)
    measure = infer_measure(source.supplier_unit)

    units_per_carton: Decimal | None = None
    cartons_per_pallet: Decimal | None = None
    reasons: list[str] = []

    if mapping_count != 1:
        reasons.append(f"Item number is mapped to {mapping_count} workbook rows.")
    if widget_match_status == "missing":
        reasons.append("Item number was not found in the Widget item master.")
    elif widget_match_status == "collision":
        reasons.append("Cleaned item number matches more than one Widget item.")

    raw_numeric_fields = (
        ("quantity per supplier unit", source.quantity_per_supplier_unit_raw, quantity_per_unit),
        ("quantity per carton", source.quantity_per_carton_raw, quantity_per_carton),
        ("quantity per pallet", source.quantity_per_pallet_raw, quantity_per_pallet),
        ("FOB price", source.fob_price_raw, fob_price),
    )
    for label, raw_value, parsed_value in raw_numeric_fields:
        if raw_value and parsed_value is None:
            reasons.append(f"Could not parse {label}: {raw_value!r}.")
        elif parsed_value is not None and parsed_value < ZERO:
            reasons.append(f"{label.title()} is negative.")

    if quantity_per_unit and quantity_per_unit > ZERO and quantity_per_carton:
        ratio = quantity_per_carton / quantity_per_unit
        units_per_carton = ratio
        if ratio < Decimal("1"):
            reasons.append("Carton quantity is smaller than one supplier unit.")
        elif not _near_integer(ratio):
            reasons.append(
                "Carton quantity is not an exact whole number of supplier units "
                f"({ _decimal_text(ratio) })."
            )
    elif not quantity_per_unit and not quantity_per_carton:
        reasons.append("Both quantity-per-unit and quantity-per-carton are blank.")

    if quantity_per_carton and quantity_per_carton > ZERO and quantity_per_pallet:
        ratio = quantity_per_pallet / quantity_per_carton
        cartons_per_pallet = ratio
        if ratio < Decimal("1"):
            reasons.append("Pallet quantity is smaller than one carton.")
        elif not _near_integer(ratio):
            reasons.append(
                "Pallet quantity is not an exact whole number of cartons "
                f"({ _decimal_text(ratio) })."
            )

    blocking = mapping_count != 1 or widget_match_status != "matched"
    parse_problem = any(
        text.startswith("Could not parse")
        or "not an exact whole number" in text
        or text.endswith("is negative.")
        or "smaller than" in text
        for text in reasons
    )
    missing_pack = not quantity_per_unit or not quantity_per_carton
    if blocking or parse_problem:
        preview_status = "review"
        proposed_action = "review_before_field_design"
    elif missing_pack:
        preview_status = "partial"
        proposed_action = "retain_raw_values_only"
    else:
        preview_status = "ready"
        proposed_action = "ready_for_field_design"

    roll_length = quantity_per_unit if measure == "metre" else None
    metres_per_carton = quantity_per_carton if measure == "metre" else None

    return YuchangPackingPreviewRow(
        source_row=source.source_row,
        item_number=source.item_number,
        item_number_key=clean_item_key(source.item_number),
        workbook_mapping_count=mapping_count,
        widget_match_status=widget_match_status,
        widget_item_number=str(matched.get("item_number") or ""),
        widget_item_name=str(matched.get("item_name") or ""),
        supplier_link_status=str(link.get("match_status") or "not_linked"),
        supplier_item_number=str(link.get("supplier_item_number") or ""),
        supplier_description_raw=source.supplier_description,
        size_raw=source.size,
        colour_raw=source.colour,
        supplier_unit_raw=source.supplier_unit,
        quantity_per_supplier_unit_raw=source.quantity_per_supplier_unit_raw,
        label_description_raw=source.label_description,
        quantity_per_carton_raw=source.quantity_per_carton_raw,
        quantity_per_pallet_raw=source.quantity_per_pallet_raw,
        fob_price_raw=source.fob_price_raw,
        inferred_measure=measure,
        parsed_quantity_per_supplier_unit=_decimal_text(quantity_per_unit),
        parsed_quantity_per_carton=_decimal_text(quantity_per_carton),
        parsed_supplier_units_per_carton=_decimal_text(units_per_carton),
        parsed_roll_or_spool_length_metres=_decimal_text(roll_length),
        parsed_metres_per_carton=_decimal_text(metres_per_carton),
        parsed_quantity_per_pallet=_decimal_text(quantity_per_pallet),
        parsed_cartons_per_pallet=_decimal_text(cartons_per_pallet),
        parsed_fob_price=_decimal_text(fob_price),
        preview_status=preview_status,
        review_reason=" ".join(reasons),
        proposed_action=proposed_action,
    )
