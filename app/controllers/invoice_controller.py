"""
Invoice Controller for Smart Pay
Handles invoice CRUD, upload, dashboard, and status workflow
"""
from datetime import datetime, date
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import re

from app.models.database import SessionLocal, Invoice, InvoiceFeatures
from app.services.csv_service import CSVService
from app.services.paths import UPLOAD_DIR

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
csv_service = CSVService()

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════

def get_current_user(request):
    uid = request.session.get("user_id")
    if not uid: return None
    return {"user_id": uid, "email": request.session.get("email", "")}


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
        r'Invoice\s*#\s*[-\u2014]?\s*([A-Za-z0-9][\w\-/]+)',
        r'Invoice\s*(?:No\.?|Number|ID|Ref)[:\-\u2014\s]+([A-Za-z0-9][\w\-/]+)',
        r'\bInv[.\s]+(?:No|#|Ref)[:\-\u2014\s]+([A-Za-z0-9][\w\-/]+)',
        r'Bill\s*(?:No\.?|Number|#)[:\-\u2014\s]+([A-Za-z0-9][\w\-/]+)',
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
    for sym, code in [('\u20ac','EUR'),('EUR','EUR'),('$','USD'),('USD','USD'),
                      ('\u00a3','GBP'),('GBP','GBP'),('AUD','AUD'),('CAD','CAD'),
                      ('CHF','CHF'),('\u00a5','JPY'),('JPY','JPY'),('\u20b9','INR'),('INR','INR')]:
        if sym in text:
            currency = code
            break

    patterns = [
        r'Invoice\s*Amount\s*[-\u2014:]?\s*[\u20ac$\u00a3\u00a5\u20b9]?\s*([\d.,]+)',
        r'(?:Grand\s*Total|Total|Amount\s*Due|Balance\s*Due)\s*[-\u2014:]?\s*[\u20ac$\u00a3\u00a5\u20b9]?\s*([\d.,]+)',
        r'[\u20ac$\u00a3\u00a5\u20b9]\s*([\d.,]+)',
        r'([\d.,]+)\s*[\u20ac$\u00a3\u00a5\u20b9]',
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
    for label in [r'Invoice\s*Date\s*[-\u2014:]?\s*', r'Date\s*[-\u2014:]?\s*']:
        for dp in date_re:
            m = re.search(label + f'({dp})', text, re.I)
            if m: inv_date = m.group(1).strip(); break
        if inv_date != 'N/A': break

    for label in [r'Due\s*Date\s*[-\u2014:]?\s*', r'Next\s*Billing\s*Date\s*[-\u2014:]?\s*',
                  r'Pay\s*By\s*[-\u2014:]?\s*']:
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
        r'(?:Attn|Attention|Name)\s*[-\u2014:]?\s*([A-Za-z][\w\s\'.&,-]{1,40})',
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
    for p in [r'VAT\s*(?:Number|No\.?|ID)\s*[-\u2014:]?\s*([A-Z]{2}[A-Z0-9]{8,12})',
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
def dashboard(request: Request):
    db = SessionLocal()
    try:
        # Auto-detect overdue invoices
        _auto_update_statuses(db)

        invoices = db.query(Invoice).order_by(Invoice.created_at.desc()).all()
        stats = csv_service.get_stats()
        all_customers = csv_service.get_all_customers()
        high_risk_customers = csv_service.get_high_risk_customers(0.6)

        low_risk = medium_risk = high_risk = paid_on_time = paid_late = not_paid = 0
        for c in all_customers:
            rel = c.get('payment_reliability', 0.5)
            if rel >= 0.7: low_risk += 1
            elif rel >= 0.4: medium_risk += 1
            else: high_risk += 1
            paid_on_time += c.get('paid_on_time', 0)
            paid_late += c.get('paid_late', 0)
            not_paid += c.get('not_paid', 0)

        avg_rel = stats.get('avg_reliability', 0.5)
        avg_reliability = int(avg_rel * 100) if isinstance(avg_rel, float) and avg_rel <= 1 else int(avg_rel)

        # Invoice status counts
        status_pending = sum(1 for i in invoices if (i.status or 'pending') == 'pending')
        status_paid = sum(1 for i in invoices if i.status == 'paid')
        status_overdue = sum(1 for i in invoices if i.status == 'overdue')
        status_disputed = sum(1 for i in invoices if i.status == 'disputed')

        user = get_current_user(request)

        return templates.TemplateResponse("dashboard.html", {
            "request": request, "current_user": user,
            "total_invoices": len(invoices),
            "total_customers": stats.get('total_customers', 0),
            "high_risk_count": len(high_risk_customers), "avg_reliability": avg_reliability,
            "recent_invoices": invoices[:10], "high_risk_customers": high_risk_customers[:5],
            "low_risk_count": low_risk, "medium_risk_count": medium_risk,
            "paid_on_time": paid_on_time, "paid_late": paid_late, "not_paid": not_paid,
            "status_pending": status_pending, "status_paid": status_paid,
            "status_overdue": status_overdue, "status_disputed": status_disputed,
        })
    finally:
        db.close()


@router.get("/upload")
def upload_page(request: Request):
    user = get_current_user(request)
    return templates.TemplateResponse("upload.html", {"request": request, "current_user": user})
@router.post("/update-status/{invoice_id}")

@router.post("/upload")
async def upload_invoice(request: Request, file: UploadFile = File(...)):
    db = SessionLocal()
    try:
        allowed = {'.pdf', '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif'}
        ext = Path(file.filename).suffix.lower()
        if ext not in allowed:
            return templates.TemplateResponse("upload.html", {
                "request": request, "error": f"Invalid file type '{ext}'. Allowed: PDF, JPG, PNG"
            })

        safe_name = re.sub(r'[^a-zA-Z0-9._\- ]', '', file.filename).strip() or "upload" + ext
        file_path = UPLOAD_DIR / safe_name
        content = await file.read()

        if len(content) > 10 * 1024 * 1024:
            return templates.TemplateResponse("upload.html", {
                "request": request, "error": "File too large. Maximum size is 10MB."
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

        features = InvoiceFeatures(invoice_id=invoice.id)
        db.add(features)
        db.commit()

        return RedirectResponse(f"/view-invoice/{invoice.id}?uploaded=1", status_code=303)
    except Exception as e:
        print(f"Upload error: {e}")
        return templates.TemplateResponse("upload.html", {"request": request, "error": str(e)})
    finally:
        db.close()


@router.get("/view-data")
def view_all_invoices(request: Request):
    db = SessionLocal()
    try:
        _auto_update_statuses(db)
        invoices = db.query(Invoice).order_by(Invoice.created_at.desc()).all()
        user = get_current_user(request)

        # Count by status
        status_counts = {'pending': 0, 'paid': 0, 'overdue': 0, 'disputed': 0}
        for inv in invoices:
            s = inv.status or 'pending'
            if s in status_counts: status_counts[s] += 1

        return templates.TemplateResponse("view_data.html", {
            "request": request, "current_user": user,
            "invoices": invoices, "total": len(invoices),
            **status_counts,
        })
    finally:
        db.close()


@router.get("/view-invoice/{invoice_id}")
def view_invoice(request: Request, invoice_id: int):
    db = SessionLocal()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            return templates.TemplateResponse("error.html", {
                "request": request, "error_code": 404,
                "message": "Invoice not found", "description": f"Invoice #{invoice_id} doesn't exist."
            }, status_code=404)

        # Auto-check overdue
        new_status = _check_overdue(invoice)
        if new_status != invoice.status:
            invoice.status = new_status
            db.commit()

        customer_history = None
        if invoice.customer_name and invoice.customer_name != 'N/A':
            customer_history = csv_service.find_customer_match(invoice.customer_name)

        user = get_current_user(request)
        return templates.TemplateResponse("view_invoice.html", {
            "request": request, "current_user": user,
            "inv": invoice, "customer_history": customer_history,
        })
    finally:
        db.close()


@router.post("/update-status/{invoice_id}")
async def update_status(request: Request, invoice_id: int, status: str = Form(...)):
    """Update invoice status (paid, overdue, disputed, pending)"""
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    valid = {'pending', 'paid', 'overdue', 'disputed'}
    if status not in valid:
        return RedirectResponse(f"/view-invoice/{invoice_id}?error=Invalid+status", status_code=303)

    db = SessionLocal()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if invoice:
            invoice.status = status
            db.commit()
        return RedirectResponse(f"/view-invoice/{invoice_id}", status_code=303)
    finally:
        db.close()


@router.get("/edit-invoice/{invoice_id}")
def edit_invoice_page(request: Request, invoice_id: int):
    db = SessionLocal()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            return templates.TemplateResponse("error.html", {
                "request": request, "error_code": 404,
                "message": "Invoice not found", "description": f"Invoice #{invoice_id} doesn't exist."
            }, status_code=404)
        user = get_current_user(request)
        return templates.TemplateResponse("edit_invoice.html", {
            "request": request, "current_user": user, "inv": invoice, "invoice": invoice,
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
    due_date: str = Form(""), vat_id: str = Form("")
):
    db = SessionLocal()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            return RedirectResponse("/view-data", status_code=303)

        if invoice_number: invoice.invoice_number = sanitize(invoice_number)
        if customer_name: invoice.customer_name = sanitize(customer_name)
        if customer_email: invoice.customer_email = sanitize(customer_email)
        if customer_address: invoice.customer_address = sanitize(customer_address)
        if company_name: invoice.company_name = sanitize(company_name)
        if amount: invoice.amount = sanitize(amount)
        if currency: invoice.currency = sanitize(currency)
        if date: invoice.date = sanitize(date)
        if due_date: invoice.due_date = sanitize(due_date)
        if vat_id: invoice.vat_id = sanitize(vat_id)

        # Re-check overdue after date change
        invoice.status = _check_overdue(invoice)
        db.commit()
        return RedirectResponse(f"/view-invoice/{invoice_id}", status_code=303)
    finally:
        db.close()


@router.post("/delete-invoice")
async def delete_invoice(request: Request, invoice_id: int = Form(...)):
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