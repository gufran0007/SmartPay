"""
Invoice history import: upload -> column mapping -> deterministic validated
import.

When an AI mapping provider is configured (Gemini or Anthropic) and it
proposes a mapping that has every required field and passes
validate_mapping_sample, upload imports immediately -- no manual step in
the happy path. Manual review (import_review.html, confirmed via
import_confirm) is the fallback: no AI provider configured, the AI call
failed, or its proposal didn't pass validation. Either way, nothing is
EVER imported off of an unvalidated mapping, and the deterministic
validation runs identically regardless of which path produced the mapping.

The LLM only ever sees headers + a sample of rows and only ever proposes a
mapping; it never touches row import or any calculation.
"""
import json
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, UploadFile, File
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.models.database import SessionLocal, PendingImport
from app.services.auth import require_account, CurrentUser
from app.services.paths import get_account_upload_dir
from app.services.llm_mapping import (
    CANONICAL_FIELDS, REQUIRED_CANONICAL_FIELDS,
    AnthropicColumnMapper, GeminiColumnMapper, HeuristicColumnMapper, MappingProviderError,
)
from app.services.invoice_import import (
    read_headers_and_sample, apply_mapping_and_import, validate_mapping_sample,
    error_report_csv, UnsupportedFileError,
)
from app.services.ml_service import MLService
from app.services.error_logging import log_exception, logger

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# name -> (env var to check for a key, provider class). Order matters: this
# is also the auto-detect preference order when MAPPING_PROVIDER isn't set
# (or is set to something whose key isn't actually present).
_MAPPING_PROVIDERS = [
    ("gemini", "GEMINI_API_KEY", GeminiColumnMapper),
    ("anthropic", "ANTHROPIC_API_KEY", AnthropicColumnMapper),
]


def _select_mapping_provider_name() -> str:
    """Which provider _get_mapping_provider will actually construct.

    MAPPING_PROVIDER=gemini|anthropic|manual can force a choice, but only
    ever wins if that provider's key is actually configured -- forcing a
    provider with no key falls through to auto-detect rather than raising,
    same as leaving MAPPING_PROVIDER unset. Auto-detect prefers Gemini, then
    Anthropic, then the no-key heuristic fallback, which never needs a key
    and therefore never fails to be selectable.
    """
    forced = os.environ.get("MAPPING_PROVIDER", "").strip().lower()
    available = {name: bool(os.environ.get(env_var)) for name, env_var, _ in _MAPPING_PROVIDERS}

    if forced == "manual":
        return "manual"
    if forced in available and available[forced]:
        return forced

    for name, _, _ in _MAPPING_PROVIDERS:
        if available[name]:
            return name
    return "manual"


def _get_mapping_provider():
    """Swappable for tests via app.dependency_overrides.

    Never raises: falls back to the deterministic HeuristicColumnMapper
    when no configured LLM key is available, so /import works end to end
    with no key at all -- the user just does more of the column mapping by
    hand on the review screen. AnthropicColumnMapper/GeminiColumnMapper
    (both make network calls) are only ever constructed when their key is
    actually present. This used to construct AnthropicColumnMapper()
    unconditionally, which raised MappingProviderError from right here
    whenever ANTHROPIC_API_KEY was missing -- during dependency resolution,
    before the route body's own try/except ever got a chance to catch it --
    so a missing key reached the browser as a bare 500 instead of the
    "mapping failed" redirect other LLM failures get.
    """
    name = _select_mapping_provider_name()
    if name == "gemini":
        return GeminiColumnMapper()
    if name == "anthropic":
        return AnthropicColumnMapper()
    return HeuristicColumnMapper()


_active_mapping_provider = _select_mapping_provider_name()
if _active_mapping_provider == "manual":
    logger.info("SMARTPAY_STARTUP column_mapping=manual reason=no_llm_key_configured")
else:
    logger.info("SMARTPAY_STARTUP column_mapping=%s", _active_mapping_provider)

FIELD_LABELS = {
    "invoice_id": "Invoice ID",
    "customer_id": "Customer ID",
    "customer_name": "Customer name",
    "invoice_date": "Invoice date",
    "due_date": "Due date",
    "paid_date": "Paid date",
    "amount": "Amount",
    "currency": "Currency",
    "payment_terms": "Payment terms",
}


def render(request: Request, template: str, status_code: int = 200, **context):
    return templates.TemplateResponse(template, {"request": request, **context}, status_code=status_code)


def _write_error_report_if_any(account_id: int, import_id: int, result) -> None:
    if not result.errors:
        return
    account_dir = get_account_upload_dir(account_id)
    (account_dir / f"import_errors_{import_id}.csv").write_text(error_report_csv(result.errors), encoding="utf-8")


def _mapping_dict(proposal) -> dict:
    return {m.canonical_field: m.source_column for m in proposal.mappings}


@router.get("/import")
def import_upload_page(request: Request, current_user: CurrentUser = Depends(require_account)):
    return render(request, "import_upload.html", current_user=current_user)


@router.post("/import")
async def import_upload(
    request: Request,
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(require_account),
    mapping_provider=Depends(_get_mapping_provider),
):
    safe_name = "".join(c for c in file.filename if c.isalnum() or c in "._- ") or "upload"
    account_dir = get_account_upload_dir(current_user.account_id)
    file_path = account_dir / f"import_{safe_name}"

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        return RedirectResponse("/import?error=File+too+large.+Maximum+size+is+10MB.", status_code=303)
    file_path.write_bytes(content)

    try:
        headers, sample_rows = read_headers_and_sample(file_path)
    except UnsupportedFileError as e:
        log_exception(request, where="import_upload.read_headers", filename=file.filename)
        return RedirectResponse(f"/import?error={str(e)[:100]}", status_code=303)

    # "AI active" is a property of configuration (is a real key present?),
    # not of the injected mapping_provider object -- so tests can override
    # the provider with a fake while still controlling this via env vars,
    # and production always agrees with what _get_mapping_provider actually
    # constructed from the same env state.
    ai_active = _select_mapping_provider_name() != "manual"
    ai_failed = False
    validation_errors: list = []

    if ai_active:
        try:
            proposal = mapping_provider.propose_mapping(headers, sample_rows)
        except MappingProviderError:
            # Falls back to the heuristic's guess rather than bouncing the
            # user back to re-upload -- we already have the file and the
            # headers, no reason to make them start over.
            log_exception(request, where="import_upload.propose_mapping", filename=file.filename)
            ai_failed = True
            proposal = HeuristicColumnMapper().propose_mapping(headers, sample_rows)
        else:
            mapping = _mapping_dict(proposal)
            missing = [f for f in REQUIRED_CANONICAL_FIELDS if not mapping.get(f)]
            if missing:
                validation_errors = [f"Required field '{FIELD_LABELS[f]}' has no confident column match" for f in missing]
            else:
                validation_errors = validate_mapping_sample(file_path, mapping)
    else:
        proposal = mapping_provider.propose_mapping(headers, sample_rows)  # HeuristicColumnMapper never raises

    proposed_mapping = proposal.as_dict()

    db = SessionLocal()
    try:
        pending = PendingImport(
            account_id=current_user.account_id,
            filename=file.filename,
            file_path=str(file_path),
            headers=json.dumps(headers),
            sample_rows=json.dumps(sample_rows, default=str),
            proposed_mapping=json.dumps(proposed_mapping),
            status="pending_review",
        )
        db.add(pending)
        db.commit()
        db.refresh(pending)

        if ai_active and not ai_failed and not validation_errors:
            # Happy path: the AI mapped it, the mapping passed the same
            # deterministic check the manual/confirm path uses -- import
            # immediately, no manual step.
            mapping = _mapping_dict(proposal)
            result = apply_mapping_and_import(db, current_user.account_id, file_path, mapping)
            pending.status = "confirmed"
            db.commit()
            MLService(current_user.account_id).train(db)
            _write_error_report_if_any(current_user.account_id, pending.id, result)

            summary = f"Imported {result.imported} invoice(s)"
            if result.updated:
                summary += f", updated {result.updated}"
            redirect_url = f"/customers?success={quote(summary)}"
            if result.errors:
                redirect_url += f"&import_id={pending.id}"
            return RedirectResponse(redirect_url, status_code=303)

        # Manual review: no AI provider configured, the AI call failed, or
        # its proposal didn't pass validation -- the review screen shows
        # exactly why, pre-filled with whatever mapping we do have (the
        # AI's, if only column validation was the problem; the heuristic's
        # guess otherwise) so the user corrects rather than starts blank.
        review_url = f"/import/{pending.id}/review"
        if validation_errors:
            review_url += f"?error={quote(' | '.join(validation_errors)[:400])}"
        elif ai_failed:
            review_url += f"?error={quote('AI mapping failed -- please map the columns below')}"
        return RedirectResponse(review_url, status_code=303)
    finally:
        db.close()


@router.get("/import/{import_id}/review")
def import_review(request: Request, import_id: int, current_user: CurrentUser = Depends(require_account)):
    db = SessionLocal()
    try:
        pending = db.query(PendingImport).filter(PendingImport.id == import_id).first()
        if not pending:
            return render(request, "error.html", status_code=404, current_user=current_user, error_code=404,
                          message="Import not found", description="This import doesn't exist.")

        headers = json.loads(pending.headers)
        proposed = json.loads(pending.proposed_mapping)
        fields = [
            {
                "name": f,
                "label": FIELD_LABELS[f],
                "required": f in REQUIRED_CANONICAL_FIELDS,
                "source_column": proposed.get(f, {}).get("source_column"),
                "confidence": proposed.get(f, {}).get("confidence", 0.0),
            }
            for f in CANONICAL_FIELDS
        ]
        return render(
            request, "import_review.html", current_user=current_user,
            pending=pending, headers=headers, fields=fields,
        )
    finally:
        db.close()


@router.post("/import/{import_id}/confirm")
async def import_confirm(request: Request, import_id: int, current_user: CurrentUser = Depends(require_account)):
    form = await request.form()
    mapping = {f: (form.get(f"map_{f}") or None) for f in CANONICAL_FIELDS}

    db = SessionLocal()
    try:
        pending = db.query(PendingImport).filter(PendingImport.id == import_id).first()
        if not pending:
            return RedirectResponse("/import", status_code=303)

        missing = [f for f in REQUIRED_CANONICAL_FIELDS if not mapping.get(f)]
        if missing:
            return RedirectResponse(
                f"/import/{import_id}/review?error=Required+fields+need+a+column:+{','.join(missing)}",
                status_code=303,
            )

        # Deterministic, non-LLM sanity check on the mapping itself, before
        # anything is imported -- runs the same way regardless of whether
        # the mapping came from an LLM, the heuristic fallback, or the
        # user's own manual edits on the review screen.
        column_errors = validate_mapping_sample(Path(pending.file_path), mapping)
        if column_errors:
            return RedirectResponse(
                f"/import/{import_id}/review?error={quote(' | '.join(column_errors)[:400])}",
                status_code=303,
            )

        result = apply_mapping_and_import(db, current_user.account_id, Path(pending.file_path), mapping)
        pending.status = "confirmed"
        db.commit()

        # Retrain immediately so new paid invoices are reflected without a
        # separate manual step -- see Stage 3 (per-tenant prediction).
        MLService(current_user.account_id).train(db)
        _write_error_report_if_any(current_user.account_id, import_id, result)

        return render(
            request, "import_result.html", current_user=current_user,
            imported=result.imported, updated=result.updated,
            error_count=len(result.errors), import_id=import_id,
            has_errors=bool(result.errors),
        )
    finally:
        db.close()


@router.get("/import/{import_id}/errors")
def import_errors_download(request: Request, import_id: int, current_user: CurrentUser = Depends(require_account)):
    db = SessionLocal()
    try:
        pending = db.query(PendingImport).filter(PendingImport.id == import_id).first()
        if not pending:
            return RedirectResponse("/import", status_code=303)
    finally:
        db.close()

    account_dir = get_account_upload_dir(current_user.account_id)
    error_report_path = account_dir / f"import_errors_{import_id}.csv"
    if not error_report_path.exists():
        return RedirectResponse("/import", status_code=303)

    return Response(
        content=error_report_path.read_text(encoding="utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=import_errors_{import_id}.csv"},
    )
