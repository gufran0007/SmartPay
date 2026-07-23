"""
Invoice history import: upload -> LLM-proposed column mapping -> user
confirms/corrects -> deterministic validated import.

The LLM only ever sees headers + a sample of rows and only ever proposes a
mapping; it never touches row import or any calculation. Nothing is
imported until the user confirms the mapping on the review screen.
"""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request, UploadFile, File
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.models.database import SessionLocal, PendingImport
from app.services.auth import require_account, CurrentUser
from app.services.paths import get_account_upload_dir
from app.services.llm_mapping import (
    CANONICAL_FIELDS, REQUIRED_CANONICAL_FIELDS,
    AnthropicColumnMapper, MappingProviderError,
)
from app.services.invoice_import import (
    read_headers_and_sample, apply_mapping_and_import, error_report_csv,
    UnsupportedFileError,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

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


def _get_mapping_provider():
    """Swappable for tests via app.dependency_overrides."""
    return AnthropicColumnMapper()


def render(request: Request, template: str, status_code: int = 200, **context):
    return templates.TemplateResponse(template, {"request": request, **context}, status_code=status_code)


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
        return RedirectResponse(f"/import?error={str(e)[:100]}", status_code=303)

    try:
        proposal = mapping_provider.propose_mapping(headers, sample_rows)
        proposed_mapping = proposal.as_dict()
    except MappingProviderError as e:
        return RedirectResponse(f"/import?error=Mapping+failed:+{str(e)[:100]}", status_code=303)

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
        return RedirectResponse(f"/import/{pending.id}/review", status_code=303)
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

        result = apply_mapping_and_import(db, current_user.account_id, Path(pending.file_path), mapping)
        pending.status = "confirmed"
        db.commit()

        error_report_path = None
        if result.errors:
            account_dir = get_account_upload_dir(current_user.account_id)
            error_report_path = account_dir / f"import_errors_{import_id}.csv"
            error_report_path.write_text(error_report_csv(result.errors), encoding="utf-8")

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
