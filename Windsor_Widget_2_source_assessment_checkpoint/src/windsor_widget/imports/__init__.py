"""Review-first source parsers and data contracts."""

from windsor_widget.imports.contracts import SOURCE_CONTRACTS, SourceContract
from windsor_widget.imports.matching import (
    CustomerGroupProposal,
    CustomerPriceFileProposal,
    ItemSupplierProposal,
    MatchAlternative,
    PriceFileReference,
    PurchaseEvidence,
    load_customer_price_file_references,
    most_recent_purchase_by_item,
    propose_customer_groups,
    propose_customer_price_file_matches,
    propose_item_supplier_matches,
)
from windsor_widget.imports.myob_text import (
    MyobFileInspection,
    ParsedFile,
    ParsedRow,
    ParseIssue,
    inspect_myob_text,
    iter_myob_rows,
    parse_myob_text,
)
from windsor_widget.imports.staging import (
    DuplicateImportBatchError,
    StagingSummary,
    stage_myob_file,
)

__all__ = [
    "MyobFileInspection",
    "ParsedFile",
    "ParsedRow",
    "ParseIssue",
    "SOURCE_CONTRACTS",
    "SourceContract",
    "DuplicateImportBatchError",
    "CustomerGroupProposal",
    "CustomerPriceFileProposal",
    "ItemSupplierProposal",
    "MatchAlternative",
    "PriceFileReference",
    "PurchaseEvidence",
    "StagingSummary",
    "inspect_myob_text",
    "iter_myob_rows",
    "load_customer_price_file_references",
    "most_recent_purchase_by_item",
    "parse_myob_text",
    "propose_customer_groups",
    "propose_customer_price_file_matches",
    "propose_item_supplier_matches",
    "stage_myob_file",
]
