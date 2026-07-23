"""
Per-account payment-late prediction.

Each account gets its own Random Forest, trained only on that account's own
paid invoices (Invoice rows with paid_date set) — never on another
account's data, and never on any shared demo/reference dataset. Below
MIN_TRAINING_INVOICES paid invoices there isn't enough signal to trust a
model over a coin flip, so predictions fall back to a plain, explainable
payment-terms heuristic instead. Every prediction records which of the two
produced it, so the UI never has to guess or overstate what happened.
"""
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
from sqlalchemy.orm import Session

from app.models.database import Invoice
from app.services.paths import MODELS_DIR

# Below this many labeled (paid) invoices, an 80/20 train/test split leaves
# fewer than ~4-5 held-out examples — too few to report a meaningful
# accuracy, and too little data to fit a model that would actually beat a
# simple heuristic. 20 is also low enough that a genuinely active account
# crosses it within its first few months of use, rather than being stuck
# on the heuristic indefinitely.
MIN_TRAINING_INVOICES = 20

MODE_ML = "ml"
MODE_HEURISTIC = "heuristic"


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _days_between(start: Optional[date], end: Optional[date]) -> Optional[int]:
    if start is None or end is None:
        return None
    return (end - start).days


def _customer_key(invoice: Invoice) -> str:
    return (invoice.customer_id or invoice.customer_name or "").strip().lower()


def is_late(invoice: Invoice) -> bool:
    paid = _parse_date(invoice.paid_date)
    due = _parse_date(invoice.due_date)
    if paid is None or due is None:
        return False
    return paid > due


class PredictionResult:
    def __init__(self, probability: float, label: int, mode: str, invoices_seen: int, held_out_accuracy: Optional[float]):
        self.probability = probability
        self.label = label
        self.mode = mode
        self.invoices_seen = invoices_seen
        self.held_out_accuracy = held_out_accuracy


class MLService:
    """Scoped to one account: construct with that account's id, and only
    that account's paid invoices are ever read or trained on. Model files
    are per-account (payment_predictor_account_<id>.pkl) so two accounts'
    models can never mix."""

    def __init__(self, account_id: int):
        self.account_id = account_id
        self.model_path = MODELS_DIR / f"payment_predictor_account_{account_id}.pkl"
        self.model = None
        self.held_out_accuracy: Optional[float] = None
        self.trained_on = 0
        self._load_model()

    def _load_model(self):
        if not self.model_path.exists():
            return
        try:
            bundle = joblib.load(self.model_path)
            self.model = bundle["model"]
            self.held_out_accuracy = bundle.get("held_out_accuracy")
            self.trained_on = bundle.get("trained_on", 0)
        except Exception as e:
            print(f"Could not load model for account {self.account_id}: {e}")
            self.model = None

    def _paid_invoices(self, db: Session) -> List[Invoice]:
        return (
            db.query(Invoice)
            .filter(Invoice.account_id == self.account_id, Invoice.paid_date.isnot(None))
            .all()
        )

    def _current_paid_count(self, db: Session) -> int:
        return (
            db.query(Invoice)
            .filter(Invoice.account_id == self.account_id, Invoice.paid_date.isnot(None))
            .count()
        )

    def _customer_late_ratio_excluding_self(self, invoice_id: int, same_customer: List[Invoice]) -> float:
        others = [inv for inv in same_customer if inv.id != invoice_id]
        if not others:
            return 0.5  # no history for this customer -> neutral, not a guess dressed as data
        return sum(1 for inv in others if is_late(inv)) / len(others)

    def _feature_vector(self, invoice: Invoice, customer_late_ratio: float, avg_amount: float) -> List[float]:
        days_to_due = _days_between(_parse_date(invoice.date), _parse_date(invoice.due_date))
        return [
            (invoice.amount_float / avg_amount) if avg_amount > 0 else 1.0,
            (days_to_due if days_to_due is not None else 30) / 30.0,
            customer_late_ratio,
        ]

    def train(self, db: Session) -> Tuple[int, str]:
        """Retrains this account's model from its own paid invoices.
        Returns (paid_invoice_count, human-readable status message)."""
        invoices = self._paid_invoices(db)
        self.trained_on = len(invoices)

        if len(invoices) < MIN_TRAINING_INVOICES:
            self.model = None
            self.held_out_accuracy = None
            if self.model_path.exists():
                self.model_path.unlink()
            return len(invoices), (
                f"Only {len(invoices)} paid invoice(s) on file — need at least "
                f"{MIN_TRAINING_INVOICES} to train a model. Using the payment-terms "
                f"heuristic instead."
            )

        by_customer: Dict[str, List[Invoice]] = {}
        for inv in invoices:
            by_customer.setdefault(_customer_key(inv), []).append(inv)

        avg_amount = sum(inv.amount_float for inv in invoices) / len(invoices)

        X = np.array([
            self._feature_vector(
                inv,
                self._customer_late_ratio_excluding_self(inv.id, by_customer[_customer_key(inv)]),
                avg_amount,
            )
            for inv in invoices
        ])
        y = np.array([1 if is_late(inv) else 0 for inv in invoices])

        stratify = y if len(set(y.tolist())) > 1 else None
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=stratify
        )

        model = RandomForestClassifier(n_estimators=100, max_depth=6, random_state=42, class_weight="balanced")
        model.fit(X_train, y_train)
        accuracy = float(accuracy_score(y_test, model.predict(X_test))) if len(X_test) else None

        self.model = model
        self.held_out_accuracy = accuracy

        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": model, "held_out_accuracy": accuracy, "trained_on": len(invoices)}, self.model_path)

        acc_text = f"{accuracy:.1%} held-out accuracy" if accuracy is not None else "held-out accuracy not available"
        return len(invoices), f"Trained on {len(invoices)} paid invoices ({acc_text})."

    def predict(self, db: Session, invoice: Invoice) -> PredictionResult:
        """Predicts for a single invoice. Uses the trained model if one
        exists for this account (cleared automatically if it fell below
        the training threshold); otherwise the transparent heuristic."""
        if self.model is not None:
            paid_invoices = self._paid_invoices(db)
            avg_amount = sum(inv.amount_float for inv in paid_invoices) / len(paid_invoices) if paid_invoices else 0.0

            key = _customer_key(invoice)
            same_customer = [inv for inv in paid_invoices if _customer_key(inv) == key]
            customer_ratio = (
                sum(1 for inv in same_customer if is_late(inv)) / len(same_customer)
                if same_customer else 0.5  # unknown customer -> neutral prior, not fabricated confidence
            )

            vector = self._feature_vector(invoice, customer_ratio, avg_amount)
            proba = float(self.model.predict_proba([vector])[0][1])
            label = 1 if proba > 0.5 else 0
            return PredictionResult(proba, label, MODE_ML, self.trained_on, self.held_out_accuracy)

        # No trained model: report the CURRENT paid-invoice count, not
        # self.trained_on, which is only meaningful right after train() ran
        # in this same instance. A freshly-constructed instance that never
        # called train() would otherwise always report 0.
        return self._heuristic_predict(invoice, self._current_paid_count(db))

    def _heuristic_predict(self, invoice: Invoice, invoices_seen: int) -> PredictionResult:
        """Transparent, non-ML fallback used below the training threshold.
        Shorter payment terms are a real, explainable risk signal — the
        less runway before the due date, the more likely a customer slips
        past it — so this flags tight terms and, secondarily, unusually
        large amounts. It is never labeled as ML anywhere in the UI."""
        days_to_due = _days_between(_parse_date(invoice.date), _parse_date(invoice.due_date))

        if days_to_due is None:
            risk = 0.5
        elif days_to_due <= 7:
            risk = 0.6
        elif days_to_due <= 14:
            risk = 0.45
        else:
            risk = 0.3

        if invoice.amount_float > 10000:
            risk = min(1.0, risk + 0.1)

        label = 1 if risk > 0.5 else 0
        return PredictionResult(risk, label, MODE_HEURISTIC, invoices_seen, None)

    def customer_summary(self, db: Session, invoice: Invoice) -> Dict:
        """Paid-invoice history for this invoice's customer within the
        account, excluding the invoice itself. Used to explain a
        prediction without exposing model internals."""
        paid_invoices = self._paid_invoices(db)
        key = _customer_key(invoice)
        same_customer = [inv for inv in paid_invoices if _customer_key(inv) == key and inv.id != invoice.id]
        if not same_customer:
            return {"paid_count": 0, "late_count": 0, "on_time_count": 0, "avg_delay_days": 0.0}

        late = [inv for inv in same_customer if is_late(inv)]
        delays = []
        for inv in late:
            paid = _parse_date(inv.paid_date)
            due = _parse_date(inv.due_date)
            if paid and due:
                delays.append((paid - due).days)

        return {
            "paid_count": len(same_customer),
            "late_count": len(late),
            "on_time_count": len(same_customer) - len(late),
            "avg_delay_days": (sum(delays) / len(delays)) if delays else 0.0,
        }

    def get_model_info(self, db: Session) -> Dict:
        if self.model is not None:
            return {
                "mode": MODE_ML,
                "invoices_seen": self.trained_on,
                "held_out_accuracy": self.held_out_accuracy,
            }
        return {
            "mode": MODE_HEURISTIC,
            "invoices_seen": self._current_paid_count(db),
            "held_out_accuracy": None,
            "invoices_needed": MIN_TRAINING_INVOICES,
        }
