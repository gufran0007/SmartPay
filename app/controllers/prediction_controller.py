"""Prediction Controller for Smart Pay"""
from datetime import datetime, date
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.models.database import SessionLocal, InvoiceFeatures, Invoice
from app.services.ml_service import MLService
from app.services.csv_service import CSVService
from app.services.paths import DATA_DIR

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
ml_service = MLService()
csv_service = CSVService()


def get_current_user(request):
    uid = request.session.get("user_id")
    if not uid: return None
    return {"user_id": uid, "email": request.session.get("email", "")}


def _parse_date(date_str):
    if not date_str or date_str == 'N/A': return None
    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
                "%b %d, %Y", "%B %d, %Y", "%d %b, %Y", "%d %B %Y",
                "%b %d %Y", "%d %b %Y"]:
        try: return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError: continue
    return None


def _days_overdue(due_date_str):
    d = _parse_date(due_date_str)
    if not d: return 0
    return max(0, (date.today() - d).days)


def _safe_amount(invoice):
    try: return float(str(invoice.amount).replace(',','').replace('$','').replace('E','').replace('£',''))
    except: return 0.0


@router.get("/ml/train")
def train_model(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)
    db = SessionLocal()
    try:
        csv_files = list(DATA_DIR.glob("*.csv"))
        csv_path = csv_files[0] if csv_files else None
        count, message = ml_service.train_from_db(db, csv_path)
        return templates.TemplateResponse("ml_status.html", {
            "request": request, "current_user": get_current_user(request),
            "message": message, "count": count, "success": count > 0
        })
    except Exception as e:
        return templates.TemplateResponse("ml_status.html", {
            "request": request, "current_user": get_current_user(request),
            "message": f"Training failed: {e}", "count": 0, "success": False
        })
    finally:
        db.close()


@router.get("/ml/predict/{invoice_id}")
def predict_payment(request: Request, invoice_id: int):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)
    db = SessionLocal()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            return RedirectResponse("/view-data", status_code=303)

        proba, label = ml_service.predict_for_invoice(db, invoice_id)

        # Overdue boost
        overdue_days = _days_overdue(invoice.due_date)
        if overdue_days > 0:
            proba = min(1.0, proba + min(0.4, overdue_days * 0.01))
            if proba > 0.5: label = 1

        # Unknown customer adjustment
        customer_match = csv_service.find_customer_match(invoice.customer_name or "")
        if not customer_match:
            proba = max(proba, 0.45)
            due = _parse_date(invoice.due_date)
            if due:
                days_until = (due - date.today()).days
                if days_until <= 0: proba = max(proba, 0.65)
                elif days_until <= 3: proba = max(proba, 0.55)
                elif days_until <= 7: proba = max(proba, 0.50)
            else:
                proba = max(proba, 0.50)
            amt = _safe_amount(invoice)
            if amt > 10000: proba = min(1.0, proba + 0.15)
            elif amt > 5000: proba = min(1.0, proba + 0.10)
            elif amt > 1000: proba = min(1.0, proba + 0.05)
            label = 1 if proba > 0.5 else 0

        # Save prediction
        features = db.query(InvoiceFeatures).filter(InvoiceFeatures.invoice_id == invoice_id).first()
        if features:
            features.predicted_probability = proba
            features.predicted_label = label
        else:
            db.add(InvoiceFeatures(invoice_id=invoice_id, predicted_probability=proba, predicted_label=label))
        db.commit()

        return RedirectResponse(f"/view-invoice/{invoice_id}", status_code=303)
    except Exception as e:
        print(f"Prediction error: {e}")
        return RedirectResponse(f"/view-invoice/{invoice_id}?error=Prediction+failed", status_code=303)
    finally:
        db.close()


@router.get("/ml/analytics")
def ml_analytics(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)
    db = SessionLocal()
    try:
        all_features = db.query(InvoiceFeatures).filter(InvoiceFeatures.predicted_label.isnot(None)).all()
        total_predictions = len(all_features)
        high_risk = sum(1 for f in all_features if f.predicted_label == 1)
        low_risk = total_predictions - high_risk
        avg_confidence = sum(f.predicted_probability for f in all_features) / max(total_predictions, 1)

        stats = csv_service.get_stats()
        all_customers = csv_service.get_all_customers()
        high_risk_customers = csv_service.get_high_risk_customers()

        low_risk_cust = sum(1 for c in all_customers if c.get('payment_reliability', 0) >= 0.7)
        med_risk_cust = sum(1 for c in all_customers if 0.4 <= c.get('payment_reliability', 0) < 0.7)
        high_risk_cust = sum(1 for c in all_customers if c.get('payment_reliability', 0) < 0.4)

        total_on_time = sum(c.get('paid_on_time', 0) for c in all_customers)
        total_late = sum(c.get('paid_late', 0) for c in all_customers)
        total_unpaid = sum(c.get('not_paid', 0) for c in all_customers)

        recent = db.query(Invoice).join(InvoiceFeatures).filter(
            InvoiceFeatures.predicted_label.isnot(None)
        ).order_by(Invoice.created_at.desc()).limit(10).all()

        top_risk = sorted(all_customers, key=lambda c: c.get('payment_reliability', 1))[:5]

        return templates.TemplateResponse("ml_analytics.html", {
            "request": request, "current_user": get_current_user(request),
            "total_predictions": total_predictions,
            "high_risk_count": high_risk, "low_risk_count": low_risk,
            "avg_confidence": round(avg_confidence * 100, 1),
            "total_customers": stats.get('total_customers', 0),
            "high_risk_customers": high_risk_customers[:10],
            "recent_predictions": recent,
            "model_info": ml_service.get_model_info(),
            "low_risk_cust": low_risk_cust, "med_risk_cust": med_risk_cust,
            "high_risk_cust": high_risk_cust,
            "total_on_time": total_on_time, "total_late": total_late,
            "total_unpaid": total_unpaid,
            "total_amount": round(stats.get('total_amount', 0), 2),
            "avg_reliability": stats.get('avg_reliability', 0),
            "top_risk": top_risk,
        })
    finally:
        db.close()


@router.get("/ml/explain/{invoice_id}")
def explain_prediction(request: Request, invoice_id: int):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)
    db = SessionLocal()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            return RedirectResponse("/view-data", status_code=303)

        customer_data = csv_service.find_customer_match(invoice.customer_name)
        factors = []

        overdue_days = _days_overdue(invoice.due_date)
        if overdue_days > 0:
            factors.append(("Invoice is overdue", f"{overdue_days} days past due date"))

        if customer_data:
            risk_assessment = csv_service.calculate_payment_risk(customer_data, _safe_amount(invoice))
            rel = risk_assessment['payment_reliability']

            if rel < 0.4: factors.append(("Low payment reliability", f"{rel:.0%} on-time rate"))
            elif rel < 0.7: factors.append(("Moderate payment reliability", f"{rel:.0%} on-time rate"))
            else: factors.append(("Good payment reliability", f"{rel:.0%} on-time rate"))

            if customer_data.get('avg_payment_delay', 0) > 15:
                factors.append(("History of late payments", f"Avg {customer_data['avg_payment_delay']:.0f} days late"))
            if customer_data.get('not_paid', 0) > 0:
                factors.append(("Has unpaid invoices", f"{customer_data['not_paid']} unpaid"))
            for rf in customer_data.get('risk_factors', [])[:3]:
                factors.append(("Risk factor", rf))
        else:
            risk_assessment = None
            factors.append(("New customer", "No payment history available"))
            amt = _safe_amount(invoice)
            if amt > 5000: factors.append(("Large invoice amount", f"{amt:,.2f} from unknown customer"))

        return templates.TemplateResponse("ml_explain.html", {
            "request": request, "current_user": get_current_user(request),
            "invoice": invoice, "customer_data": customer_data,
            "risk_assessment": risk_assessment, "factors": factors,
        })
    finally:
        db.close()