"""
Invoice Controller for Smart Pay
Handles invoice CRUD, upload, dashboard, and status workflow
"""
from datetime import datetime, date
from fastapi import APIRouter, Depends, Request, UploadFile, File, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import re
from uuid import uuid4
from decimal import Decimal, InvalidOperation

from app.models.database import SessionLocal, Invoice, InvoiceFeatures
from app.services.auth import require_account, CurrentUser
from app.services.csv_service import CSVService
from app.services.paths import get_account_upload_dir
from app.services.error_logging import log_exception

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# ════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════

def sanitize(value):
    if not value: return ""
    value = re.sub(r'<[^>]+>', '', str(value))
    return value.replace('<','').replace('>','').replace('"','').replace("'",'').strip()


def _parse_date(date_str):
    """Try to parse date string into a date object"""
    if not date_str or date_str == 'N/A':
        return None
    formats = [
        "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
        "%b %d, %Y", "%B %d, %Y", "%d %b, %Y", "%d %B %Y",
        "%b %d %Y", "%d %b %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _check_overdue(invoice):
    if invoice.status in ('paid', 'disputed'):
        return invoice.status

    due = _parse_date(invoice.due_date)
    if due and due < date.today():
        return 'overdue'
    return 'pending'


def _auto_update_statuses(db):
    """Scan all pending invoices and mark overdue ones"""
    pending = db.query(Invoice).filter(Invoice.status.in_(['pending', None, ''])).all()
    count = 0
    for inv in pending:
        new_status = _check_overdue(inv)
        if new_status != inv.status:
            inv.status = new_status
            count += 1
    if count > 0:
        db.commit()
    return count


def extract_text(file_path: Path) -> str:
    text = ""
    suffix = file_path.suffix.lower()
    try:
        if suffix == '.pdf':
            try:
                import fitz
                doc = fitz.open(file_path)
                for page in doc:
                    blocks = page.get_text("dict").get("blocks", [])
                    parts = []
                    for block in blocks:
                        if "lines" not in block: continue
                        for line in block["lines"]:
                            lt = " ".join(span["text"] for span in line["spans"]).strip()
                            if lt: parts.append(lt)
                    structured = "\n".join(parts)
                    plain = page.get_text() or ""
                    text += structured if len(structured) >= len(plain) else plain
                doc.close()
            except Exception as e:
                print(f"PDF error: {e}")
        elif suffix in ['.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif']:
            try:
                import pytesseract
                from PIL import Image, ImageEnhance, ImageFilter
                img = Image.open(file_path)
                if img.mode != 'L': img = img.convert('L')
                w, h = img.size
                if w < 1200:
                    scale = 1200 / w
                    img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
                img = ImageEnhance.Contrast(img).enhance(2.0)
                img = ImageEnhance.Sharpness(img).enhance(2.0)
                img = img.filter(ImageFilter.SHARPEN)
                text = pytesseract.image_to_string(img, config='--oem 3 --psm 6')
            except Exception as e:
                print(f"OCR error: {e}")
    except Exception as e:
        print(f"Extraction error: {e}")

    if text:
        text = re.sub(r'\r\n|\r', '\n', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def extract_data(file_path: Path) -> dict:
    text = extract_text(file_path)
    data = {
        'invoice_number': 'N/A', 'customer_name': 'N/A', 'customer_email': 'N/A',
        'customer_address': 'N/A', 'company_name': 'N/A', 'amount': 'N/A',
        'currency': 'N/A', 'date': 'N/A', 'due_date': 'N/A', 'vat_id': 'N/A'
    }
    if not text: return data

    data['invoice_number'] = _find_invoice_number(text)
    data['customer_email'] = _find_email(text)
    data['amount'], data['currency'] = _find_amount_and_currency(text)
    data['date'], data['due_date'] = _find_dates(text)
    data['customer_name'] = _find_customer_name(text)
    data['company_name'] = _find_company_name(text)
    data['vat_id'] = _find_vat_id(text)
    data['customer_address'] = _find_address(text)
    return data


def _find_invoice_number(text):
    patterns = [
        r'Invoice\s*#\s*[-—]?\s*([A-Za-z0-9][\w\-/]+)',
        r'Invoice\s*(?:No\.?|Number|ID|Ref)[:\-—\s]+([A-Za-z0-9][\w\-/]+)',
        r'\bInv[.\s]+(?:No|#|Ref)[:\-—\s]+([A-Za-z0-9][\w\-/]+)',
        r'Bill\s*(?:No\.?|Number|#)[:\-—\s]+([A-Za-z0-9][\w\-/]+)',
        r'\b(INV[\-/][A-Z]?[\-/]?\d{4}[\-/]\d+)\b',
        r'\b(INV[\-]?\d{3,})\b',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if len(val) >= 3 and val.lower() not in ('invoice', 'inv', 'number', 'no'):
                return val
    return 'N/A'


def _find_email(text):
    m = re.search(r'([\w.%+-]+@[\w.-]+\.[A-Za-z]{2,})', text)
    return m.group(1) if m else 'N/A'


def _find_amount_and_currency(text):
    currency = 'N/A'
    for sym, code in [('€','EUR'),('EUR','EUR'),('$','USD'),('USD','USD'),
                      ('£','GBP'),('GBP','GBP'),('AUD','AUD'),('CAD','CAD'),
                      ('CHF','CHF'),('¥','JPY'),('JPY','JPY'),('₹','INR'),('INR','INR')]:
        if sym in text:
            currency = code
            break

    patterns = [
        r'Invoice\s*Amount\s*[-—:]?\s*[€$£¥₹]?\s*([\d.,]+)',
        r'(?:Grand\s*Total|Total|Amount\s*Due|Balance\s*Due)\s*[-—:]?\s*[€$£¥₹]?\s*([\d.,]+)',
        r'[€$£¥₹]\s*([\d.,]+)',
        r'([\d.,]+)\s*[€$£¥₹]',
        r'([\d.,]+)\s*(?:EUR|USD|GBP|AUD|CAD)',
    ]
    found = []
    for p in patterns:
        for m in re.finditer(p, text, re.IGNORECASE):
            val = _parse_amount(m.group(1))
            if val and val > 0: found.append(val)
    return (str(max(found)) if found else 'N/A'), currency


def _parse_amount(raw):
    if not raw: return None
    raw = raw.strip()
    if re.match(r'^\d{1,3}(\.\d{3})*(,\d{1,2})$', raw):
        raw = raw.replace('.','').replace(',','.')
    elif ',' in raw and '.' not in raw:
        raw = raw.replace(',','.')
    elif ',' in raw and '.' in raw:
        if raw.rfind(',') > raw.rfind('.'):
            raw = raw.replace('.','').replace(',','.')
        else:
            raw = raw.replace(',','')
    try: return round(float(raw), 2)
    except: return None


def _find_dates(text):
    inv_date = due_date = 'N/A'
    date_re = [
        r'\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}',
        r'[A-Z][a-z]+\s+\d{1,2},?\s+\d{4}',
        r'\d{1,2}\s+[A-Z][a-z]+,?\s+\d{4}',
        r'\d{4}[/\-]\d{2}[/\-]\d{2}',
    ]
    for label in [r'Invoice\s*Date\s*[-—:]?\s*', r'Date\s*[-—:]?\s*']:
        for dp in date_re:
            m = re.search(label + f'({dp})', text, re.I)
            if m: inv_date = m.group(1).strip(); break
        if inv_date != 'N/A': break

    for label in [r'Due\s*Date\s*[-—:]?\s*', r'Next\s*Billing\s*Date\s*[-—:]?\s*',
                  r'Pay\s*By\s*[-—:]?\s*']:
        for dp in date_re:
            m = re.search(label + f'({dp})', text, re.I)
            if m: due_date = m.group(1).strip(); break
        if due_date != 'N/A': break

    if inv_date == 'N/A':
        all_d = []
        for dp in date_re: all_d.extend(re.findall(dp, text))
        if all_d:
            inv_date = all_d[0]
            if len(all_d) > 1 and due_date == 'N/A': due_date = all_d[1]
    return inv_date, due_date


def _find_customer_name(text):
    patterns = [
        r'(?:BILLED?\s*TO|Bill\s*To|Customer|Client)\s*\n\s*([A-Za-z0-9][\w\s\'.&,-]{1,40})',
        r'(?:Attn|Attention|Name)\s*[-—:]?\s*([A-Za-z][\w\s\'.&,-]{1,40})',
    ]
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            name = m.group(1).strip().split('\n')[0].strip()
            if len(name) > 1 and '@' not in name: return name
    email = _find_email(text)
    if email != 'N/A':
        local = email.split('@')[0]
        name = re.sub(r'[0-9._+-]', ' ', local).title().strip()
        if name and len(name) > 1: return name
    return 'N/A'


def _find_company_name(text):
    for line in text.split('\n')[:10]:
        line = line.strip()
        if re.search(r'\b(Ltd|LLC|Inc|Corp|GmbH|SA|SL|BV|Pty|Company|Co\b|S\.L|S\.A)', line, re.I):
            if 3 < len(line) < 80: return line
    return 'N/A'


def _find_vat_id(text):
    for p in [r'VAT\s*(?:Number|No\.?|ID)\s*[-—:]?\s*([A-Z]{2}[A-Z0-9]{8,12})',
              r'\b(ES[A-Z]\d{8})\b', r'\b(GB\d{9,12})\b', r'\b(IE\d{7}[A-Z]{1,2})\b',
              r'\b(DE\d{9})\b', r'\b(FR[A-Z0-9]{2}\d{9})\b']:
        m = re.search(p, text, re.I)
        if m: return m.group(1).strip()
    return 'N/A'


def _find_address(text):
    m = re.search(
        r'(?:BILLED?\s*TO|Bill\s*To)\s*\n(.+?)(?=\n\s*(?:INVOICE|SUBSCRIPTION|DESCRIPTION|PAYMENT|FROM|Date|Total|$))',
        text, re.I | re.DOTALL
    )
    if not m: return 'N/A'
    parts = []
    for line in m.group(1).strip().split('\n')[1:]:
        line = line.strip()
        if '@' in line: continue
        if line: parts.append(line)
    return ', '.join(parts[:3]) if parts else 'N/A'


@router.get("/dashboard")
def dashboard(request: Request, current_user: CurrentUser = Depends(require_account)):
    db = SessionLocal()
    try:
        # Auto-detect overdue invoices
        _auto_update_statuses(db)

        invoices = db.query(Invoice).order_by(Invoice.created_at.desc()).all()
        counts = {key: sum((i.status or 'pending') == key for i in invoices)
                  for key in ('pending', 'paid', 'overdue', 'disputed')}
        customers = {i.customer_name.strip().casefold() for i in invoices
                     if i.customer_name and i.customer_name.strip() not in ('', 'N/A')}
        attention = sorted((i for i in invoices if i.status == 'overdue'),
                           key=lambda i: _parse_date(i.due_date) or date.max)
        return templates.TemplateResponse("dashboard.html", {
            "request": request, "current_user": current_user,
            "total_invoices": len(invoices), "total_customers": len(customers),
            "recent_invoices": invoices[:8], "attention_invoices": attention[:5],
            "incomplete_count": sum(not i.is_complete for i in invoices),
            **{f"status_{key}": value for key, value in counts.items()},
        })
    finally:
        db.close()


@router.get("/upload")
def upload_page(request: Request, current_user: CurrentUser = Depends(require_account)):
    return templates.TemplateResponse("upload.html", {"request": request, "current_user": current_user})


@router.post("/upload")
async def upload_invoice(request: Request, file: UploadFile = File(...), current_user: CurrentUser = Depends(require_account)):
    db = SessionLocal()
    try:
        allowed = {'.pdf', '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif'}
        ext = Path(file.filename).suffix.lower()
        if ext not in allowed:
            return templates.TemplateResponse("upload.html", {
                "request": request, "current_user": current_user,
                "error": f"Invalid file type '{ext}'. Allowed: PDF, JPG, PNG"
            })

        safe_name = uuid4().hex + "_" + (re.sub(r'[^a-zA-Z0-9._\- ]', '', file.filename).strip() or "upload" + ext)
        account_dir = get_account_upload_dir(current_user.account_id)
        file_path = account_dir / safe_name
        content = await file.read()

        if len(content) > 10 * 1024 * 1024:
            return templates.TemplateResponse("upload.html", {
                "request": request, "current_user": current_user,
                "error": "File too large. Maximum size is 10MB."
            })

        with open(file_path, "wb") as f:
            f.write(content)

        extracted = extract_data(file_path)

        # Determine initial status
        initial_status = 'pending'
        due = _parse_date(extracted.get('due_date', 'N/A'))
        if due and due < date.today():
            initial_status = 'overdue'

        invoice = Invoice(
            account_id=current_user.account_id,
            filename=safe_name,
            invoice_number=sanitize(extracted['invoice_number']),
            customer_name=sanitize(extracted['customer_name']),
            customer_email=sanitize(extracted['customer_email']),
            customer_address=sanitize(extracted['customer_address']),
            company_name=sanitize(extracted['company_name']),
            amount=extracted['amount'],
            currency=extracted['currency'],
            date=extracted['date'],
            due_date=extracted['due_date'],
            vat_id=sanitize(extracted['vat_id']),
            status=initial_status,
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)

        features = InvoiceFeatures(invoice_id=invoice.id, account_id=current_user.account_id)
        db.add(features)
        db.commit()

        return RedirectResponse(f"/view-invoice/{invoice.id}?uploaded=1", status_code=303)
    except Exception as e:
        log_exception(request, where="upload_invoice", filename=file.filename)
        return templates.TemplateResponse("upload.html", {"request": request, "current_user": current_user, "error": str(e)})
    finally:
        db.close()


@router.get("/view-data")
def view_all_invoices(request: Request, current_user: CurrentUser = Depends(require_account)):
    db = SessionLocal()
    try:
        _auto_update_statuses(db)
        invoices = db.query(Invoice).order_by(Invoice.created_at.desc()).all()

        # Count by status
        status_counts = {'pending': 0, 'paid': 0, 'overdue': 0, 'disputed': 0}
        for inv in invoices:
            s = inv.status or 'pending'
            if s in status_counts: status_counts[s] += 1

        return templates.TemplateResponse("view_data.html", {
            "request": request, "current_user": current_user,
            "invoices": invoices, "total": len(invoices),
            **status_counts,
        })
    finally:
        db.close()


@router.get("/view-invoice/{invoice_id}")
def view_invoice(request: Request, invoice_id: int, current_user: CurrentUser = Depends(require_account)):
    db = SessionLocal()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            return templates.TemplateResponse("error.html", {
                "request": request, "current_user": current_user, "error_code": 404,
                "message": "Invoice not found", "description": f"Invoice #{invoice_id} doesn't exist."
            }, status_code=404)

        # Auto-check overdue
        new_status = _check_overdue(invoice)
        if new_status != invoice.status:
            invoice.status = new_status
            db.commit()

        customer_history = None
        if invoice.customer_name and invoice.customer_name != 'N/A':
            csv_service = CSVService(upload_dir=get_account_upload_dir(current_user.account_id))
            customer_history = csv_service.find_customer_match(invoice.customer_name)

        return templates.TemplateResponse("view_invoice.html", {
            "request": request, "current_user": current_user,
            "inv": invoice, "customer_history": customer_history,
        })
    finally:
        db.close()


@router.post("/update-status/{invoice_id}")
async def update_status(request: Request, invoice_id: int, status: str = Form(...), current_user: CurrentUser = Depends(require_account)):
    """Update invoice status (paid, overdue, disputed, pending)"""
    valid = {'pending', 'paid', 'overdue', 'disputed'}
    if status not in valid:
        return RedirectResponse(f"/view-invoice/{invoice_id}?error=Invalid+status", status_code=303)

    db = SessionLocal()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if invoice:
            if status == 'paid':
                if invoice.status != 'paid' or not invoice.paid_date:
                    invoice.paid_date = date.today().isoformat()
            else:
                invoice.paid_date = None
            invoice.status = status
            invoice.status = _check_overdue(invoice)
            db.commit()
        return RedirectResponse(f"/view-invoice/{invoice_id}", status_code=303)
    finally:
        db.close()


@router.get("/edit-invoice/{invoice_id}")
def edit_invoice_page(request: Request, invoice_id: int, current_user: CurrentUser = Depends(require_account)):
    db = SessionLocal()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            return templates.TemplateResponse("error.html", {
                "request": request, "current_user": current_user, "error_code": 404,
                "message": "Invoice not found", "description": f"Invoice #{invoice_id} doesn't exist."
            }, status_code=404)
        return templates.TemplateResponse("edit_invoice.html", {
            "request": request, "current_user": current_user, "inv": invoice, "invoice": invoice,
        })
    finally:
        db.close()


@router.post("/update-invoice/{invoice_id}")
async def update_invoice(
    request: Request, invoice_id: int,
    invoice_number: str = Form(""), customer_name: str = Form(""),
    customer_email: str = Form(""), customer_address: str = Form(""),
    company_name: str = Form(""), amount: str = Form(""),
    currency: str = Form(""), date: str = Form(""),
    due_date: str = Form(""), vat_id: str = Form(""),
    current_user: CurrentUser = Depends(require_account),
):
    db = SessionLocal()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            return RedirectResponse("/view-data", status_code=303)

        values = {
            "invoice_number": invoice_number.strip(), "customer_name": customer_name.strip(),
            "customer_email": customer_email.strip(), "customer_address": customer_address.strip(),
            "company_name": company_name.strip(), "amount": amount.strip(),
            "currency": currency.strip(), "date": date.strip(),
            "due_date": due_date.strip(), "vat_id": vat_id.strip(),
        }
        errors = {}
        if values["amount"] and values["amount"].upper() != "N/A":
            normalized_amount = re.sub(r"[,$€£\s]", "", values["amount"])
            try:
                parsed_amount = Decimal(normalized_amount)
                if not parsed_amount.is_finite() or parsed_amount < 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                errors["amount"] = "Enter a valid amount of zero or more."
        if values["customer_email"] and values["customer_email"].upper() != "N/A" and not re.fullmatch(
            r"[^\s@]+@[^\s@]+\.[^\s@]+", values["customer_email"]
        ):
            errors["customer_email"] = "Enter a valid email address."
        for field in ("date", "due_date"):
            value = values[field]
            if value and value.upper() != "N/A" and _parse_date(value) is None:
                errors[field] = "Enter a valid date."
        if values["invoice_number"] and not re.fullmatch(r"[A-Za-z0-9_/-]{1,50}", values["invoice_number"]):
            errors["invoice_number"] = "Use up to 50 letters, numbers, hyphens, underscores or slashes."
        if values["vat_id"] and values["vat_id"].upper() != "N/A" and not re.fullmatch(
            r"[A-Za-z0-9-]{5,20}", values["vat_id"]
        ):
            errors["vat_id"] = "Use 5–20 letters, numbers or hyphens."

        if errors:
            return templates.TemplateResponse("edit_invoice.html", {
                "request": request, "current_user": current_user, "inv": invoice,
                "invoice": invoice, "errors": errors, "form": values,
            }, status_code=422)

        limits = {"invoice_number": 50, "customer_name": 200, "customer_email": 254,
                  "customer_address": 500, "company_name": 200, "currency": 3, "vat_id": 20}
        for field, limit in limits.items():
            values[field] = sanitize(values[field])[:limit] if values[field] else "N/A"
        if values["amount"]:
            values["amount"] = sanitize(values["amount"])
        else:
            values["amount"] = "N/A"
        for field in ("date", "due_date"):
            values[field] = sanitize(values[field]) if values[field] else "N/A"

        for field, value in values.items():
            setattr(invoice, field, value)
        invoice.status = _check_overdue(invoice)
        db.commit()
        return RedirectResponse(f"/view-invoice/{invoice_id}", status_code=303)
    finally:
        db.close()


@router.post("/delete-invoice")
async def delete_invoice(request: Request, invoice_id: int = Form(...), current_user: CurrentUser = Depends(require_account)):
    db = SessionLocal()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if invoice:
            db.query(InvoiceFeatures).filter(InvoiceFeatures.invoice_id == invoice_id).delete()
            db.delete(invoice)
            db.commit()
        return RedirectResponse("/view-data", status_code=303)
    finally:
        db.close()
