import os
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import smtplib
from email.mime.text import MIMEText

from app.controllers import auth_controller, invoice_controller, prediction_controller, csv_controller
from app.models.database import SessionLocal, Invoice

app = FastAPI(title="Smart Pay Invoice System", version="2.0.0")
from starlette.middleware.sessions import SessionMiddleware
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

# ===== EMAIL CONFIG =====
GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]

@app.get("/")
def root(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "current_user": None,
    })

@app.post("/send-reminder/{invoice_id}")
async def send_reminder(invoice_id: int, customer_email: str = Form(...)):
    db = SessionLocal()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            return RedirectResponse(f"/view-invoice/{invoice_id}?error=Invoice+not+found", status_code=303)

        is_urgent = invoice.features and invoice.features.predicted_label == 1

        if is_urgent:
            subject = f"⚠️ URGENT: Payment Overdue - Invoice #{invoice_id}"
            body = f"Dear {invoice.customer_name},\n\nURGENT: Invoice #{invoice_id} for {invoice.amount} is OVERDUE.\nDue Date: {invoice.due_date}\n\nPlease pay immediately.\n\nSmart Pay"
        else:
            subject = f"📧 Reminder: Invoice #{invoice_id} Due Soon"
            body = f"Dear {invoice.customer_name},\n\nReminder: Invoice #{invoice_id} for {invoice.amount} is due on {invoice.due_date}.\n\nThank you!\nSmart Pay"

        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = GMAIL_USER
        msg['To'] = customer_email

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.send_message(msg)
        server.quit()

        return RedirectResponse(f"/view-invoice/{invoice_id}?success=Reminder+sent", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/view-invoice/{invoice_id}?error=Email+failed", status_code=303)
    finally:
        db.close()

@app.exception_handler(404)
async def not_found(request: Request, exc):
    return templates.TemplateResponse("error.html", {"request": request, "error_code": 404, "message": "Page not found", "description": "Page doesn't exist."}, status_code=404)

@app.exception_handler(500)
async def server_error(request: Request, exc):
    return templates.TemplateResponse("error.html", {"request": request, "error_code": 500, "message": "Server Error", "description": "Something went wrong."}, status_code=500)
