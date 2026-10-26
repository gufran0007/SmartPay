import os
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, Form, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import smtplib
import re
from datetime import date
from app.controllers.invoice_controller import _parse_date
from email.mime.text import MIMEText

from app.controllers import auth_controller, invoice_controller, prediction_controller, csv_controller, import_controller
from app.models.database import SessionLocal, Invoice
from app.services.auth import require_account, RequireLoginRedirect, CurrentUser, AccountScopeMiddleware
from app.services.error_logging import log_exception

app = FastAPI(title="Smart Pay Invoice System", version="2.0.0")
from starlette.middleware.sessions import SessionMiddleware

# Added in this order on purpose: Starlette makes the LAST-added
# middleware the OUTERMOST one, and AccountScopeMiddleware reads
# request.session, which only exists once SessionMiddleware has run.
# So AccountScopeMiddleware must be added first (inner), SessionMiddleware
# second (outer) so it decodes the cookie before control reaches it.
app.add_middleware(AccountScopeMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ["SESSION_SECRET"],
    max_age=1800,
    same_site="lax",
    https_only=False,
)

static_path = Path("app/static")
static_path.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")

# ===== ROUTERS =====
app.include_router(auth_controller.router)
app.include_router(invoice_controller.router)
app.include_router(prediction_controller.router)
app.include_router(csv_controller.router)
app.include_router(import_controller.router)

# ===== EMAIL CONFIG =====
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

@app.get("/")
def root(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse("landing.html", {
        "request": request,
    })

@app.post("/send-reminder/{invoice_id}")
async def send_reminder(invoice_id: int, customer_email: str = Form(...), current_user: CurrentUser = Depends(require_account)):
    db = SessionLocal()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            return RedirectResponse(f"/view-invoice/{invoice_id}?error=Invoice+not+found", status_code=303)

        if invoice.status == "paid":
            return RedirectResponse(f"/view-invoice/{invoice_id}?error=Paid+invoices+do+not+need+reminders", status_code=303)
        saved_email = (invoice.customer_email or "").strip()
        recipient = (customer_email or "").strip()
        if (not saved_email or saved_email.upper() == "N/A"
                or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", saved_email)
                or recipient.casefold() != saved_email.casefold()):
            return RedirectResponse(f"/view-invoice/{invoice_id}?error=Update+the+invoice+email+before+sending", status_code=303)
        if not GMAIL_USER or not GMAIL_APP_PASSWORD:
            return RedirectResponse(f"/view-invoice/{invoice_id}?error=Email+reminders+are+not+configured", status_code=303)
        due = _parse_date(invoice.due_date)
        is_urgent = due is not None and due < date.today()

        if is_urgent:
            subject = f"⚠️ URGENT: Payment Overdue - Invoice #{invoice_id}"
            body = f"Dear {invoice.customer_name},\n\nURGENT: Invoice #{invoice_id} for {invoice.amount} is OVERDUE.\nDue Date: {invoice.due_date}\n\nPlease pay immediately.\n\nSmart Pay"
        else:
            subject = f"📧 Reminder: Invoice #{invoice_id} Due Soon"
            body = f"Dear {invoice.customer_name},\n\nReminder: Invoice #{invoice_id} for {invoice.amount} is due on {invoice.due_date}.\n\nThank you!\nSmart Pay"

        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = GMAIL_USER
        msg['To'] = saved_email

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
            server.starttls()
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.send_message(msg)

        return RedirectResponse(f"/view-invoice/{invoice_id}?success=Reminder+sent", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/view-invoice/{invoice_id}?error=Email+failed", status_code=303)
    finally:
        db.close()

@app.exception_handler(RequireLoginRedirect)
async def require_login_redirect(request: Request, exc: RequireLoginRedirect):
    return RedirectResponse("/login", status_code=303)

@app.exception_handler(404)
async def not_found(request: Request, exc):
    return templates.TemplateResponse("error.html", {"request": request, "error_code": 404, "message": "Page not found", "description": "Page doesn't exist."}, status_code=404)

@app.exception_handler(500)
async def server_error(request: Request, exc):
    log_exception(request, where="unhandled", exc_type=type(exc).__name__)
    return templates.TemplateResponse("error.html", {"request": request, "error_code": 500, "message": "Server Error", "description": "Something went wrong."}, status_code=500)
