"""
Deterministic CSV/Excel invoice-history import.

Once the user confirms a column mapping (see llm_mapping.py for how it's
proposed), everything from here on is plain, non-LLM code: read every row,
validate it against the canonical schema, import the valid ones, and report
the rest. Import is never all-or-nothing — every row is judged on its own,
so one bad row never blocks the good ones, and every rejected row shows up
in the error report rather than silently vanishing. Re-uploads update
existing rows by invoice_id within the account instead of duplicating them.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy.orm import Session

from app.models.database import Invoice
from app.services.llm_mapping import REQUIRED_CANONICAL_FIELDS
from app.services.date_parsing import infer_day_first, parse_canonical_date

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
DATE_FIELDS = ("invoice_date", "due_date", "paid_date")


class UnsupportedFileError(Exception):
    """Wrong file type, unreadable, or empty."""


@dataclass
class RowError:
    row_number: int  # 1-indexed to match the spreadsheet; header is row 1
    invoice_id: Optional[str]
    field: str
    message: str


@dataclass
class ImportResult:
    imported: int = 0
    updated: int = 0
    errors: list = field(default_factory=list)


def _read_dataframe(file_path: Path) -> pd.DataFrame:
    suffix = file_path.suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise UnsupportedFileError(f"Unsupported file type '{suffix}'. Upload a CSV or Excel file.")
    try:
        if suffix == ".csv":
            df = pd.read_csv(file_path, dtype=str)
        else:
            df = pd.read_excel(file_path, dtype=str)
    except pd.errors.EmptyDataError:
        raise UnsupportedFileError("File is empty")
    except Exception as e:
        raise UnsupportedFileError(f"Could not read file: {e}")
    if df.shape[1] == 0:
        raise UnsupportedFileError("File has no columns")
    return df


def read_headers_and_sample(file_path: Path, sample_size: int = 20):
    """Returns (headers, sample_rows) for the LLM mapping proposal."""
    df = _read_dataframe(file_path)
    headers = [str(c) for c in df.columns]
    sample_df = df.head(sample_size).where(pd.notnull(df.head(sample_size)), None)
    sample_rows = sample_df.to_dict(orient="records")
    return headers, sample_rows


def _clean_str(v) -> Optional[str]:
    if v is None:
        return None
    try:
        if isinstance(v, float) and pd.isna(v):
            return None
    except TypeError:
        pass
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "n/a", "nat"):
        return None
    return s


_AMOUNT_STRIP_RE = re.compile(r'[^\d,.\-]')


def parse_canonical_amount(raw) -> Optional[float]:
    """Numeric coercion for the amount field. Handles thousand separators in
    either US (1,234.56) or EU (1.234,56) style, currency symbols, and a
    leading minus sign."""
    s = _clean_str(raw)
    if s is None:
        return None
    negative = s.strip().startswith('-')
    s = _AMOUNT_STRIP_RE.sub('', s).lstrip('-')
    if not s:
        return None

    if ',' in s and '.' in s:
        if s.rfind(',') > s.rfind('.'):
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '')
    elif ',' in s:
        parts = s.split(',')
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            s = s.replace(',', '.')
        else:
            s = s.replace(',', '')

    try:
        value = float(s)
    except ValueError:
        return None
    return -value if negative else value


NUMERIC_CANONICAL_FIELDS = ("amount",)
VALIDATION_SAMPLE_SIZE = 20


def validate_mapped_columns(df: pd.DataFrame, mapping: dict, sample_size: int = VALIDATION_SAMPLE_SIZE) -> list:
    """Pre-import sanity check on the CONFIRMED mapping itself — runs once,
    before any row is imported, on every path (AI-proposed, heuristic, or
    fully hand-edited by the user), since a wrong column can come from any
    of them.

    This is deliberately not the same thing as apply_mapping_and_import's
    per-row validation: that one tolerates a handful of bad individual rows
    (a messy date here, a typo there) and imports everything else, which is
    the right behavior for real-world data. This one is a coarser, earlier
    check for "the wrong column got mapped entirely" -- e.g. customer_name
    accidentally mapped to amount -- which per-row validation would only
    ever report as N individual row failures, never as one clear "this
    column doesn't look right" message. So it only fires when EVERY
    sampled, non-null value in a typed column fails to parse: a handful of
    bad values among otherwise-good data is exactly what the row-level
    import already handles, and re-rejecting the whole mapping for that
    would contradict this module's "one bad row never blocks the good
    ones" design.

    Pure and deterministic -- no LLM call, no I/O -- so it runs identically
    regardless of which ColumnMappingProvider produced (or didn't produce)
    the mapping. Returns a list of human-readable error strings, one per
    bad column; empty if nothing looks wrong.
    """
    errors = []

    for canonical in NUMERIC_CANONICAL_FIELDS:
        col = mapping.get(canonical)
        if not col or col not in df.columns:
            continue
        sample = [v for v in df[col].head(sample_size).tolist() if _clean_str(v) is not None]
        if not sample:
            continue
        bad = [v for v in sample if parse_canonical_amount(v) is None]
        if bad and len(bad) == len(sample):
            shown = ", ".join(repr(_clean_str(v)) for v in bad[:5])
            errors.append(f"Column '{col}' mapped to '{canonical}' contains non-numeric values: {shown}")

    for canonical in DATE_FIELDS:
        col = mapping.get(canonical)
        if not col or col not in df.columns:
            continue
        sample = [v for v in df[col].head(sample_size).tolist() if _clean_str(v) is not None]
        if not sample:
            continue
        day_first = infer_day_first(sample)
        bad = [v for v in sample if parse_canonical_date(v, day_first) is None]
        if bad and len(bad) == len(sample):
            shown = ", ".join(repr(_clean_str(v)) for v in bad[:5])
            errors.append(f"Column '{col}' mapped to '{canonical}' contains unparseable dates: {shown}")

    return errors


def validate_mapping_sample(file_path: Path, mapping: dict, sample_size: int = VALIDATION_SAMPLE_SIZE) -> list:
    """File-reading wrapper around validate_mapped_columns for use from the
    confirm route. See that function for what's actually being checked."""
    df = _read_dataframe(file_path)
    return validate_mapped_columns(df, mapping, sample_size)


def apply_mapping_and_import(db: Session, account_id: int, file_path: Path, mapping: dict) -> ImportResult:
    """`mapping` is {canonical_field: source_column_name_or_None}, as
    confirmed by the user on the review screen."""
    df = _read_dataframe(file_path)
    result = ImportResult()

    day_first = {}
    for canonical in DATE_FIELDS:
        col = mapping.get(canonical)
        if col and col in df.columns:
            day_first[canonical] = infer_day_first(df[col].tolist())

    seen_invoice_ids: dict = {}
    rows_to_import = []

    for idx, row in df.iterrows():
        row_number = idx + 2  # +1 for header row, +1 for 1-indexing
        raw = {
            canonical: (row[col] if col and col in df.columns else None)
            for canonical, col in mapping.items()
        }
        invoice_id = _clean_str(raw.get("invoice_id"))

        missing = [f for f in REQUIRED_CANONICAL_FIELDS if not _clean_str(raw.get(f))]
        if missing:
            for f in missing:
                result.errors.append(RowError(row_number, invoice_id, f, f"Missing required field: {f}"))
            continue

        if invoice_id in seen_invoice_ids:
            result.errors.append(RowError(
                row_number, invoice_id, "invoice_id",
                f"Duplicate invoice_id '{invoice_id}' (first seen on row {seen_invoice_ids[invoice_id]})",
            ))
            continue
        seen_invoice_ids[invoice_id] = row_number

        invoice_date = parse_canonical_date(raw.get("invoice_date"), day_first.get("invoice_date", True))
        if invoice_date is None:
            result.errors.append(RowError(row_number, invoice_id, "invoice_date", f"Unparseable date: {raw.get('invoice_date')!r}"))
            continue

        due_date = parse_canonical_date(raw.get("due_date"), day_first.get("due_date", True))
        if due_date is None:
            result.errors.append(RowError(row_number, invoice_id, "due_date", f"Unparseable date: {raw.get('due_date')!r}"))
            continue

        paid_date_raw = _clean_str(raw.get("paid_date"))
        paid_date = None
        if paid_date_raw:
            paid_date = parse_canonical_date(paid_date_raw, day_first.get("paid_date", True))
            if paid_date is None:
                result.errors.append(RowError(row_number, invoice_id, "paid_date", f"Unparseable date: {paid_date_raw!r}"))
                continue
            if paid_date < invoice_date:
                result.errors.append(RowError(row_number, invoice_id, "paid_date", "paid_date is before invoice_date"))
                continue

        amount = parse_canonical_amount(raw.get("amount"))
        if amount is None:
            result.errors.append(RowError(row_number, invoice_id, "amount", f"Not a number: {raw.get('amount')!r}"))
            continue

        rows_to_import.append({
            "invoice_id": invoice_id,
            "customer_id": _clean_str(raw.get("customer_id")),
            "customer_name": _clean_str(raw.get("customer_name")),
            "invoice_date": invoice_date,
            "due_date": due_date,
            "paid_date": paid_date,
            "amount": amount,
            "currency": _clean_str(raw.get("currency")),
            "payment_terms": _clean_str(raw.get("payment_terms")),
        })

    for row in rows_to_import:
        existing = db.query(Invoice).filter(Invoice.invoice_number == row["invoice_id"]).first()
        if existing:
            _apply_row(existing, row)
            result.updated += 1
        else:
            invoice = Invoice(account_id=account_id)
            _apply_row(invoice, row)
            db.add(invoice)
            result.imported += 1

    db.commit()
    return result


def _apply_row(invoice: Invoice, row: dict):
    invoice.invoice_number = row["invoice_id"]
    invoice.customer_id = row["customer_id"]
    invoice.customer_name = row["customer_name"] or "N/A"
    invoice.date = row["invoice_date"].isoformat()
    invoice.due_date = row["due_date"].isoformat()
    invoice.amount = f"{row['amount']:.2f}"
    invoice.currency = row["currency"] or "N/A"
    invoice.payment_terms = row["payment_terms"]
    if row["paid_date"] is not None:
        invoice.paid_date = row["paid_date"].isoformat()
        invoice.status = "paid"
    elif invoice.status is None:
        invoice.status = "pending"


def error_report_csv(errors) -> str:
    lines = ["row_number,invoice_id,field,message"]
    for e in errors:
        invoice_id = (e.invoice_id or "").replace('"', '""')
        message = e.message.replace('"', '""')
        lines.append(f'{e.row_number},"{invoice_id}",{e.field},"{message}"')
    return "\n".join(lines) + "\n"
