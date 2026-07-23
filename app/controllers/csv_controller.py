"""
CSV Controller for Smart Pay
Handles CSV file uploads for customer payment history
"""
from fastapi import APIRouter, Depends, Request, UploadFile, File
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
import pandas as pd

from app.models.database import SessionLocal, Invoice
from app.services.auth import require_account, CurrentUser
from app.services.csv_service import CSVService
from app.services.paths import get_account_upload_dir

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def render(request: Request, template: str, **context):
    """Render a template with common context"""
    return templates.TemplateResponse(template, {"request": request, **context})


def safe_float(v) -> float:
    """Safely convert value to float"""
    try:
        if v is None or v == "N/A" or str(v).lower() in ['nan', 'none', '']:
            return 0.0
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def get_risk_label(reliability: float) -> str:
    """Convert reliability score to risk label"""
    if reliability >= 0.8:
        return "Low"
    elif reliability >= 0.6:
        return "Medium"
    else:
        return "High"


@router.get("/upload-csv")
def upload_csv_page(request: Request, current_user: CurrentUser = Depends(require_account)):
    """Display CSV upload page"""
    return render(request, "upload_csv.html", current_user=current_user)


@router.post("/upload-csv")
async def upload_csv(request: Request, file: UploadFile = File(...), current_user: CurrentUser = Depends(require_account)):
    """Handle CSV file upload"""
    if not file.filename.lower().endswith('.csv'):
        return RedirectResponse("/upload-csv?error=Invalid+file+type.+Only+CSV+files+allowed.", status_code=303)

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        return RedirectResponse("/upload-csv?error=File+too+large.+Maximum+size+is+10MB.", status_code=303)

    try:
        account_dir = get_account_upload_dir(current_user.account_id)
        file_path = account_dir / file.filename
        file_path.write_bytes(content)

        df = pd.read_csv(file_path)
        csv_service = CSVService(upload_dir=account_dir)
        csv_service.load_customer_data(df, file.filename)

        return RedirectResponse("/dashboard?success=CSV+data+loaded+successfully", status_code=303)

    except Exception as e:
        print(f"CSV upload error: {e}")
        return RedirectResponse(f"/upload-csv?error=Failed+to+process+CSV:+{str(e)[:50]}", status_code=303)


@router.get("/customers")
def customers_page(request: Request, current_user: CurrentUser = Depends(require_account)):
    """Display all customers with payment statistics"""
    db = SessionLocal()
    try:
        csv_service = CSVService(upload_dir=get_account_upload_dir(current_user.account_id))
        invoices = db.query(Invoice).all()

        # Aggregate invoice data by customer name
        db_stats = {}
        for inv in invoices:
            name = inv.customer_name
            if not name or name == "N/A":
                continue

            key = name.strip().lower()
            if key not in db_stats:
                db_stats[key] = {
                    "name": name,
                    "company": inv.company_name if inv.company_name != "N/A" else "",
                    "email": inv.customer_email if inv.customer_email != "N/A" else "",
                    "total_invoices_db": 0,
                    "total_amount_db": 0.0,
                }

            db_stats[key]["total_invoices_db"] += 1
            db_stats[key]["total_amount_db"] += safe_float(inv.amount)

        # Get CSV customers
        csv_customers = csv_service.get_all_customers()
        all_customers = {}

        # Process CSV customers first
        for c in csv_customers:
            name = (c.get("name") or "").strip()
            if not name:
                continue

            key = name.lower()
            reliability = c.get("payment_reliability", csv_service.calculate_payment_reliability(c))

            all_customers[key] = {
                "name": name,
                "company": c.get("company") or "",
                "email": c.get("email") or "",
                "payment_reliability": float(reliability),
                "reliability_percent": f"{round(float(reliability) * 100)}%",
                "risk_level": get_risk_label(float(reliability)),
                "total_invoices": int(c.get("total_invoices", 0)),
                "total_amount": float(c.get("total_amount", 0.0)),
                "avg_payment_delay": float(c.get("avg_payment_delay", 0)),
                "paid_on_time": int(c.get("paid_on_time", 0)),
                "paid_late": int(c.get("paid_late", 0)),
                "not_paid": int(c.get("not_paid", 0)),
                "has_csv_data": True,
                "total_invoices_db": 0,
                "total_amount_db": 0.0,
            }

        # Merge DB stats
        for key, stats in db_stats.items():
            if key in all_customers:
                # Merge into existing CSV customer
                all_customers[key]["total_invoices_db"] = stats["total_invoices_db"]
                all_customers[key]["total_amount_db"] = stats["total_amount_db"]

                # Fill missing fields
                if not all_customers[key]["email"] and stats["email"]:
                    all_customers[key]["email"] = stats["email"]
                if not all_customers[key]["company"] and stats["company"]:
                    all_customers[key]["company"] = stats["company"]
            else:
                # DB-only customer (no CSV history)
                reliability = 0.5  # Default for unknown
                all_customers[key] = {
                    "name": stats["name"],
                    "company": stats["company"],
                    "email": stats["email"],
                    "payment_reliability": reliability,
                    "reliability_percent": f"{round(reliability * 100)}%",
                    "risk_level": get_risk_label(reliability),
                    "total_invoices": 0,
                    "total_amount": 0.0,
                    "avg_payment_delay": 0.0,
                    "paid_on_time": 0,
                    "paid_late": 0,
                    "not_paid": 0,
                    "has_csv_data": False,
                    "total_invoices_db": stats["total_invoices_db"],
                    "total_amount_db": float(stats["total_amount_db"]),
                }

        # Sort by reliability (highest first)
        customers_list = sorted(
            all_customers.values(),
            key=lambda x: x.get("payment_reliability", 0),
            reverse=True
        )

        return render(
            request, "customers.html",
            current_user=current_user,
            customers=customers_list,
            total_customers=len(customers_list),
            customers_with_csv=sum(1 for c in customers_list if c.get("has_csv_data")),
        )

    finally:
        db.close()


@router.get("/customer-insights/{customer_name}")
def customer_insights(request: Request, customer_name: str, current_user: CurrentUser = Depends(require_account)):
    """View detailed insights for a specific customer"""
    from urllib.parse import unquote

    name = unquote(customer_name)
    csv_service = CSVService(upload_dir=get_account_upload_dir(current_user.account_id))
    customer = csv_service.find_customer_match(name)

    if not customer:
        return render(request, "error.html",
                     current_user=current_user,
                     error_code=404,
                     message="Customer Not Found",
                     description=f"No data found for customer: {name}")

    # Calculate risk assessment
    risk_assessment = csv_service.calculate_payment_risk(customer, 0)
    reliability = csv_service.calculate_payment_reliability(customer)

    return render(
        request, "customer_insights.html",
        current_user=current_user,
        customer=customer,
        risk_assessment=risk_assessment,
        reliability=reliability,
        reliability_percent=round(reliability * 100)
    )


@router.get("/csv-status")
def csv_status_page(request: Request, current_user: CurrentUser = Depends(require_account)):
    """Display CSV data status"""
    csv_service = CSVService(upload_dir=get_account_upload_dir(current_user.account_id))
    stats = csv_service.get_stats()

    return render(
        request, "csv_status.html",
        current_user=current_user,
        has_data=bool(csv_service.customer_history),
        customer_count=stats.get('total_customers', 0),
        record_count=stats.get('total_records', 0)
    )
