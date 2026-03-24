"""
ML Service for Smart Pay
Trains and predicts using Random Forest.
Works with IBM Finance Factoring dataset and custom datasets.
"""
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
import pandas as pd
import numpy as np
import joblib
from pathlib import Path
from typing import Tuple, Optional, Dict
from sqlalchemy.orm import Session

from app.models.database import InvoiceFeatures, Invoice
from app.services.csv_service import CSVService
from app.services.paths import MODELS_DIR, DATA_DIR


class MLService:

    def __init__(self):
        self.model = None
        self.csv_service = CSVService()
        self.model_path = MODELS_DIR / "payment_predictor.pkl"
        self._load_model()

    def _load_model(self):
        if self.model_path.exists():
            try:
                self.model = joblib.load(self.model_path)
                print(f"Loaded ML model from {self.model_path}")
            except Exception as e:
                print(f"Could not load model: {e}")

    def train_from_db(self, db: Session, csv_path: Optional[Path] = None) -> Tuple[int, str]:
        features = []
        labels = []

        for csv_file in DATA_DIR.glob("*.csv"):
            try:
                df = pd.read_csv(csv_file)

                # Normalize column names for detection
                col_map = {c: c.lower().strip() for c in df.columns}
                df_lower = df.rename(columns=col_map)
                cols = list(df_lower.columns)

                # Handle IBM format: DaysLate column
                if 'dayslate' in cols or 'days_late' in cols:
                    late_col = 'dayslate' if 'dayslate' in cols else 'days_late'
                    amt_col = next((c for c in cols if c in ('invoiceamount', 'invoice_amount', 'amount')), None)
                    settle_col = next((c for c in cols if c in ('daystosettle', 'days_to_settle')), None)
                    disputed_col = 'disputed' if 'disputed' in cols else None

                    for _, row in df_lower.iterrows():
                        days_late = float(row.get(late_col, 0)) if pd.notna(row.get(late_col)) else 0
                        label = 1 if days_late > 0 else 0
                        amount = float(row.get(amt_col, 0)) if amt_col and pd.notna(row.get(amt_col)) else 0
                        days_settle = float(row.get(settle_col, 0)) if settle_col and pd.notna(row.get(settle_col)) else 0
                        disputed = 1 if disputed_col and str(row.get(disputed_col, '')).lower() in ('1', 'yes', 'true') else 0

                        feature_vec = [
                            amount / 100000,           # normalized amount (IBM amounts are larger)
                            0.5,                       # default reliability (computed per-customer below)
                            days_late / 60,            # normalized days late
                            days_settle / 90,          # normalized days to settle
                            0.0,                       # late ratio (filled below)
                            disputed,                  # disputed flag
                            0.0                        # risk factor count
                        ]
                        features.append(feature_vec)
                        labels.append(label)

                # Handle original SmartPay format
                elif 'late_payment' in cols or 'late payment' in cols:
                    df_lower.columns = [c.replace('_', ' ') for c in df_lower.columns]
                    for _, row in df_lower.iterrows():
                        label = row.get('late payment', row.get('late_payment'))
                        if pd.isna(label):
                            continue

                        amount = float(row.get('amount', 0)) if pd.notna(row.get('amount')) else 0
                        days_late = float(row.get('days late', 0)) if pd.notna(row.get('days late')) else 0
                        past_due = float(row.get('past due ratio', 0.5)) if pd.notna(row.get('past due ratio')) else 0.5

                        feature_vec = [
                            amount / 10000,
                            1 - past_due,
                            days_late / 30,
                            0.5,
                            past_due,
                            0.0,
                            0.0
                        ]
                        features.append(feature_vec)
                        labels.append(int(label))

            except Exception as e:
                print(f"Error loading {csv_file}: {e}")

        if len(features) < 10:
            return 0, "Need at least 10 labeled samples. Upload a CSV with payment data."

        X = np.array(features)
        y = np.array(labels)

        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

        self.model = RandomForestClassifier(
            n_estimators=50, max_depth=6, random_state=42, class_weight='balanced'
        )
        self.model.fit(X_train, y_train)

        accuracy = accuracy_score(y_test, self.model.predict(X_test))

        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.model, self.model_path)

        return len(X), f"Trained with {len(X)} samples. Accuracy: {accuracy:.1%}"

    def predict_for_invoice(self, db: Session, invoice_id: int) -> Tuple[float, int]:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            return 0.5, 0

        customer_match = self.csv_service.find_customer_match(invoice.customer_name)

        try:
            amount = float(str(invoice.amount).replace(',', '').replace('$', '').replace('€', '').replace('£', ''))
        except:
            amount = 0

        if customer_match:
            risk = self.csv_service.calculate_payment_risk(customer_match, amount)
            historical_risk = risk['risk_score']
            total = max(customer_match.get('total_invoices', 1), 1)

            feature_vector = [
                amount / 100000,
                risk.get('payment_reliability', 0.5),
                customer_match.get('avg_payment_delay', 0) / 60,
                min(total, 100) / 100,
                customer_match.get('paid_late', 0) / total,
                1 if 'Disputed invoice' in customer_match.get('risk_factors', []) else 0,
                len(customer_match.get('risk_factors', [])) / 10
            ]

            if self.model is not None:
                try:
                    proba = self.model.predict_proba([feature_vector])[0][1]
                    blended = 0.6 * proba + 0.4 * historical_risk
                    return round(blended, 3), 1 if blended > 0.5 else 0
                except:
                    pass

            return round(historical_risk, 3), 1 if historical_risk > 0.5 else 0

        else:
            base_risk = 0.5
            if amount > 50000:
                base_risk = 0.6
            elif amount > 20000:
                base_risk = 0.55
            elif amount < 5000:
                base_risk = 0.4

            if self.model is not None:
                feature_vector = [amount / 100000, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0]
                try:
                    proba = self.model.predict_proba([feature_vector])[0][1]
                    blended = 0.4 * proba + 0.6 * base_risk
                    return round(blended, 3), 1 if blended > 0.5 else 0
                except:
                    pass

            return round(base_risk, 3), 1 if base_risk > 0.5 else 0

    def get_model_info(self) -> Dict:
        if self.model is None:
            return {'status': 'not_trained', 'message': 'No model. Train first.'}
        return {
            'status': 'ready',
            'model_type': type(self.model).__name__,
            'model_path': str(self.model_path)
        }