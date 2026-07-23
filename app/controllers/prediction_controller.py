"""Prediction Controller for Smart Pay

Every route here is scoped to the current account's own MLService instance
(app.services.ml_service.MLService) — its own paid invoices, its own model
file, its own training run. Nothing here reads another account's data or a
shared/reference dataset.
"""
from datetime import datetime, date
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.models.database import SessionLocal, InvoiceFeatures, Invoice
from app.services.auth import require_account, CurrentUser
from app.services.ml_service import MLService, MODE_HEURISTIC

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


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


@router.get("/ml/train")
def train_model(request: Request, current_user: CurrentUser = Depends(require_account)):
    db = SessionLocal()
    try:
        ml_service = MLService(current_user.account_id)
        count, message = ml_service.train(db)
        return templates.TemplateResponse("ml_status.html", {
            "request": request, "current_user": current_user,
            "message": message, "count": count,
            "success": ml_service.model is not None,
            "mode": ml_service.get_model_info(db)["mode"],
        })
    finally:
        db.close()


@router.get("/ml/predict/{invoice_id}")
def predict_payment(request: Request, invoice_id: int, current_user: CurrentUser = Depends(require_account)):
    db = SessionLocal()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            return RedirectResponse("/view-data", status_code=303)

        ml_service = MLService(current_user.account_id)
        result = ml_service.predict(db, invoice)

        features = db.query(InvoiceFeatures).filter(InvoiceFeatures.invoice_id == invoice_id).first()
        if not features:
            features = InvoiceFeatures(invoice_id=invoice_id, account_id=current_user.account_id)
            db.add(features)
        features.predicted_probability = result.probability
        features.predicted_label = result.label
        features.prediction_mode = result.mode
        features.model_invoices_seen = result.invoices_seen
        features.model_held_out_accuracy = result.held_out_accuracy
        db.commit()

        return RedirectResponse(f"/view-invoice/{invoice_id}", status_code=303)
    except Exception as e:
        print(f"Prediction error: {e}")
        return RedirectResponse(f"/view-invoice/{invoice_id}?error=Prediction+failed", status_code=303)
    finally:
        db.close()


@router.get("/ml/analytics")
def ml_analytics(request: Request, current_user: CurrentUser = Depends(require_account)):
    db = SessionLocal()
    try:
        all_features = db.query(InvoiceFeatures).filter(InvoiceFeatures.predicted_label.isnot(None)).all()
        total_predictions = len(all_features)
        high_risk = sum(1 for f in all_features if f.predicted_label == 1)
        low_risk = total_predictions - high_risk
        avg_confidence = sum(f.predicted_probability for f in all_features) / max(total_predictions, 1)

        ml_service = MLService(current_user.account_id)
        model_info = ml_service.get_model_info(db)

        recent = db.query(Invoice).join(InvoiceFeatures).filter(
            InvoiceFeatures.predicted_label.isnot(None)
        ).order_by(Invoice.created_at.desc()).limit(10).all()

        return templates.TemplateResponse("ml_analytics.html", {
            "request": request, "current_user": current_user,
            "total_predictions": total_predictions,
            "high_risk_count": high_risk, "low_risk_count": low_risk,
            "avg_confidence": round(avg_confidence * 100, 1),
            "recent_predictions": recent,
            "model_info": model_info,
        })
    finally:
        db.close()


@router.get("/ml/explain/{invoice_id}")
def explain_prediction(request: Request, invoice_id: int, current_user: CurrentUser = Depends(require_account)):
    db = SessionLocal()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            return RedirectResponse("/view-data", status_code=303)

        ml_service = MLService(current_user.account_id)
        summary = ml_service.customer_summary(db, invoice)
        features = invoice.features

        factors = []
        overdue_days = _days_overdue(invoice.due_date)
        if overdue_days > 0:
            factors.append(("Invoice is overdue", f"{overdue_days} days past due date"))

        if summary["paid_count"] > 0:
            on_time_rate = summary["on_time_count"] / summary["paid_count"]
            if on_time_rate < 0.4:
                factors.append(("Low payment reliability", f"{on_time_rate:.0%} on-time rate on {summary['paid_count']} past invoices"))
            elif on_time_rate < 0.7:
                factors.append(("Moderate payment reliability", f"{on_time_rate:.0%} on-time rate on {summary['paid_count']} past invoices"))
            else:
                factors.append(("Good payment reliability", f"{on_time_rate:.0%} on-time rate on {summary['paid_count']} past invoices"))
            if summary["avg_delay_days"] > 15:
                factors.append(("History of late payments", f"Avg {summary['avg_delay_days']:.0f} days late when late"))
        else:
            factors.append(("New customer", "No paid invoice history yet for this customer"))
            if invoice.amount_float > 5000:
                factors.append(("Large invoice amount", f"{invoice.amount_float:,.2f} from a customer with no track record"))

        return templates.TemplateResponse("ml_explain.html", {
            "request": request, "current_user": current_user,
            "invoice": invoice, "features": features,
            "customer_summary": summary, "factors": factors,
            "mode": features.prediction_mode if features else None,
            "invoices_seen": features.model_invoices_seen if features else 0,
            "held_out_accuracy": features.model_held_out_accuracy if features else None,
        })
    finally:
        db.close()
