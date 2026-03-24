"""
CSV Service for Smart Pay
Handles customer payment history from CSV files.
Supports both the IBM Finance Factoring dataset and custom CSV formats.
"""
import pandas as pd
import numpy as np
from pathlib import Path
import re
from typing import Dict, Optional, List
from app.services.paths import DATA_DIR, UPLOAD_DIR


class CSVService:

    def __init__(self):
        self.customer_history: Dict[str, Dict] = {}
        self.dataset_records: List[Dict] = []
        self._load_all_csv_data()

    # ════════════════════════════════════════════════════════
    #  LOADING
    # ════════════════════════════════════════════════════════

    def _load_all_csv_data(self):
        print("Loading CSV data files...")

        if DATA_DIR.exists():
            csv_files = list(DATA_DIR.glob("*.csv"))
            print(f"Found {len(csv_files)} CSV files in app/data")
            for f in csv_files:
                print(f"Loading: {f.name}")
                self._load_csv_file(f)

        if UPLOAD_DIR.exists():
            for f in UPLOAD_DIR.glob("*.csv"):
                print(f"Loading uploaded: {f.name}")
                self._load_csv_file(f)

        print(f"Loaded {len(self.customer_history)} customers, {len(self.dataset_records)} records")

    def _load_csv_file(self, csv_path: Path):
        try:
            df = pd.read_csv(csv_path)
            print(f"CSV Shape: {df.shape}, Columns: {list(df.columns)[:6]}...")

            cols_lower = [c.lower().strip() for c in df.columns]

            # Detect format: IBM dataset, labeled dataset, or customer history
            if 'dayslate' in cols_lower or 'daystosettle' in cols_lower or 'countrycode' in cols_lower:
                self._load_ibm_dataset(df, csv_path.name)
            elif 'invoice_id' in cols_lower or 'late_payment' in cols_lower or 'late payment' in cols_lower:
                self._load_dataset_csv(df, csv_path.name)
            else:
                self._load_customer_history_csv(df, csv_path.name)

        except Exception as e:
            print(f"Error loading {csv_path.name}: {e}")

    # ── IBM FINANCE FACTORING FORMAT ──

    def _load_ibm_dataset(self, df: pd.DataFrame, source: str):
        """Load IBM Finance Factoring Late Payment dataset directly"""
        try:
            # Standardize column names (case-insensitive)
            col_map = {}
            for c in df.columns:
                cl = c.lower().strip()
                if cl in ('customerid', 'customer_id'):
                    col_map[c] = 'customer_id'
                elif cl in ('invoicenumber', 'invoice_number', 'invoice_id'):
                    col_map[c] = 'invoice_id'
                elif cl in ('invoiceamount', 'invoice_amount', 'amount'):
                    col_map[c] = 'amount'
                elif cl in ('countrycode', 'country_code', 'country'):
                    col_map[c] = 'country'
                elif cl in ('dayslate', 'days_late'):
                    col_map[c] = 'days_late'
                elif cl in ('daystosettle', 'days_to_settle'):
                    col_map[c] = 'days_to_settle'
                elif cl in ('disputed',):
                    col_map[c] = 'disputed'
                elif cl in ('invoicedate', 'invoice_date'):
                    col_map[c] = 'invoice_date'
                elif cl in ('duedate', 'due_date'):
                    col_map[c] = 'due_date'
                elif cl in ('settleddate', 'settled_date'):
                    col_map[c] = 'settled_date'
                elif cl in ('customer_name', 'customer name'):
                    col_map[c] = 'customer_name'
                elif cl in ('late_payment', 'late payment'):
                    col_map[c] = 'late_payment'

            df = df.rename(columns=col_map)

            # Build customer name from ID if no name column
            if 'customer_name' not in df.columns and 'customer_id' in df.columns:
                df['customer_name'] = df['customer_id'].apply(self._id_to_company_name)

            # Build late_payment label from days_late if missing
            if 'late_payment' not in df.columns and 'days_late' in df.columns:
                df['days_late'] = pd.to_numeric(df['days_late'], errors='coerce').fillna(0)
                df['late_payment'] = (df['days_late'] > 0).astype(int)

            # Process each row
            for _, row in df.iterrows():
                name = str(row.get('customer_name', '')).strip()
                if not name or name.lower() in ('nan', 'none', ''):
                    continue

                self.dataset_records.append({
                    'customer_name': name,
                    'row': row.to_dict(),
                    'source': source
                })

                # Init customer
                if name not in self.customer_history:
                    self.customer_history[name] = {
                        'name': name,
                        'company': str(row.get('country', '')),
                        'email': '',
                        'total_invoices': 0,
                        'paid_on_time': 0,
                        'paid_late': 0,
                        'not_paid': 0,
                        'total_amount': 0.0,
                        'avg_payment_delay': 0.0,
                        'payment_history': [],
                        'risk_factors': []
                    }

                cust = self.customer_history[name]
                cust['total_invoices'] += 1
                cust['total_amount'] += self._safe_float(row.get('amount', 0))

                # Payment status
                late = int(row.get('late_payment', 0))
                if late == 1:
                    cust['paid_late'] += 1
                    delay = self._safe_float(row.get('days_late', 0))
                    if delay > 0:
                        self._update_avg_delay(cust, delay)
                else:
                    cust['paid_on_time'] += 1

                # Risk factors from IBM data
                if row.get('disputed', 0) in (1, '1', 'Yes', 'yes', True):
                    if 'Disputed invoice' not in cust['risk_factors']:
                        cust['risk_factors'].append('Disputed invoice')

            print(f"Loaded {len(df)} IBM dataset records from {source}")

        except Exception as e:
            print(f"Error processing IBM dataset: {e}")

    # ── LABELED DATASET (original Smart Pay format) ──

    def _load_dataset_csv(self, df: pd.DataFrame, source: str):
        try:
            df.columns = [col.strip().lower().replace('_', ' ') for col in df.columns]

            for _, row in df.iterrows():
                customer_name = self._extract_customer_name(row)
                if not customer_name:
                    continue

                self.dataset_records.append({
                    'customer_name': customer_name,
                    'row': row.to_dict(),
                    'source': source
                })
                self._update_customer_from_row(customer_name, row)

            print(f"Loaded {len(df)} dataset records from {source}")
        except Exception as e:
            print(f"Error processing dataset CSV: {e}")

    # ── CUSTOMER HISTORY CSV ──

    def _load_customer_history_csv(self, df: pd.DataFrame, source: str):
        for _, row in df.iterrows():
            name = self._extract_customer_name(row)
            if not name:
                continue

            if name not in self.customer_history:
                self.customer_history[name] = {
                    'name': name,
                    'company': str(row.get('company', row.get('company_name', ''))),
                    'email': str(row.get('email', row.get('customer_email', ''))),
                    'total_invoices': 0, 'paid_on_time': 0, 'paid_late': 0,
                    'not_paid': 0, 'total_amount': 0.0, 'avg_payment_delay': 0.0,
                    'payment_history': [], 'risk_factors': []
                }

            cust = self.customer_history[name]
            cust['total_invoices'] += 1

            status = str(row.get('payment_status', '')).lower()
            cust['total_amount'] += self._extract_amount(row)

            if 'paid' in status:
                if 'late' in status:
                    cust['paid_late'] += 1
                else:
                    cust['paid_on_time'] += 1
            elif 'unpaid' in status or 'overdue' in status:
                cust['not_paid'] += 1

        print(f"Loaded {len(df)} customer history records from {source}")

    # ════════════════════════════════════════════════════════
    #  HELPERS
    # ════════════════════════════════════════════════════════

    _COMPANY_NAMES = [
        "Apex Trading", "Nova Solutions", "Prime Logistics", "Atlas Group", "Summit Corp",
        "Vertex Industries", "Cascade Systems", "Meridian Tech", "Horizon Partners", "Sterling Co",
        "Pinnacle Services", "Quantum Dynamics", "Falcon Enterprises", "Eclipse Holdings", "Vanguard Ltd",
        "Nexus Global", "Titan Manufacturing", "Orion Consulting", "Phoenix Group", "Zenith Corp",
        "Delta Partners", "Sierra Trading", "Cobalt Industries", "Pacific Ventures", "Alpine Solutions",
        "Ember Technologies", "Crest Logistics", "Spark Innovations", "Forge Capital", "Ironbridge Ltd",
        "Oakwood Partners", "Silverline Corp", "Bluewave Systems", "Redstone Group", "Greenfield Co",
        "Nighthawk Trading", "Compass Enterprises", "Anchor Industries", "Lighthouse Solutions", "Beacon Corp",
        "Trident Global", "Nordic Partners", "Coral Ventures", "Sapphire Holdings", "Marble Systems",
        "Cedar Technologies", "Birchwood Ltd", "Hawthorn Group", "Rosewood Corp", "Ashford Trading",
        "Kensington Partners", "Brighton Solutions", "Stratford Industries", "Windsor Logistics", "Cambridge Co",
        "Oxford Holdings", "Bristol Dynamics", "Lancaster Group", "Sheffield Corp", "Coventry Ltd",
        "Hartwell Partners", "Millbrook Trading", "Fairview Systems", "Ridgewood Corp", "Lakeside Solutions",
        "Westgate Industries", "Eastwood Ventures", "Northfield Group", "Southbank Corp", "Hillcrest Co",
        "Broadmoor Trading", "Clearwater Ltd", "Stonewall Partners", "Ferndale Corp", "Glenmore Systems",
        "Riverton Holdings", "Moorfield Group", "Brookside Co", "Thornton Industries", "Ashbury Trading",
        "Wentworth Corp", "Dalton Solutions", "Prescott Ventures", "Hanover Group", "Belmont Ltd",
        "Carlton Partners", "Elmswood Trading", "Foxhill Corp", "Granville Systems", "Harrington Co",
        "Ingram Holdings", "Jennings Group", "Kingsley Ltd", "Langford Partners", "Montague Corp",
        "Newbury Trading", "Osborne Solutions", "Pemberton Ventures", "Radcliffe Group", "Sanderson Co",
    ]
    _id_map: Dict[str, str] = {}

    def _id_to_company_name(self, customer_id) -> str:
        """Map an anonymised customer ID to a consistent, readable company name"""
        key = str(customer_id).strip()
        if key in self._id_map:
            return self._id_map[key]
        idx = len(self._id_map) % len(self._COMPANY_NAMES)
        name = self._COMPANY_NAMES[idx]
        # If we've used all names, add a number suffix
        if len(self._id_map) >= len(self._COMPANY_NAMES):
            name = f"{name} {len(self._id_map) // len(self._COMPANY_NAMES) + 1}"
        self._id_map[key] = name
        return name

    def _extract_customer_name(self, row) -> Optional[str]:
        for col in ['customer name', 'customer_name', 'customer', 'client name', 'client', 'name', 'company name']:
            if col in row.index and pd.notna(row[col]):
                name = str(row[col]).strip()
                if name and name.lower() not in ('n/a', 'nan', 'none', ''):
                    return name
        return None

    def _update_customer_from_row(self, name: str, row):
        if name not in self.customer_history:
            self.customer_history[name] = {
                'name': name, 'company': '', 'email': '',
                'total_invoices': 0, 'paid_on_time': 0, 'paid_late': 0,
                'not_paid': 0, 'total_amount': 0.0, 'avg_payment_delay': 0.0,
                'payment_history': [], 'risk_factors': []
            }

        cust = self.customer_history[name]
        cust['total_invoices'] += 1
        cust['total_amount'] += self._safe_float(row.get('amount', 0))

        late = row.get('late payment', row.get('late_payment', None))
        if pd.notna(late):
            if int(late) == 1:
                cust['paid_late'] += 1
                delay = self._safe_float(row.get('days late', row.get('days_late', 0)))
                if delay > 0:
                    self._update_avg_delay(cust, delay)
            else:
                cust['paid_on_time'] += 1

        self._extract_risk_factors(cust, row)

        if 'industry' in row.index and pd.notna(row['industry']):
            cust['company'] = str(row['industry'])
        if 'email' in row.index and pd.notna(row['email']):
            cust['email'] = str(row['email'])

    def _extract_amount(self, row) -> float:
        for col in ['amount', 'invoice_amount', 'invoiceamount', 'total_amount', 'balance']:
            if col in row.index and pd.notna(row[col]):
                return self._safe_float(row[col])
        return 0.0

    def _safe_float(self, value) -> float:
        try:
            if value is None or str(value).lower() in ('n/a', 'nan', 'none', ''):
                return 0.0
            return float(str(value).replace(',', '').strip())
        except (ValueError, TypeError):
            return 0.0

    def _update_avg_delay(self, cust: Dict, new_delay: float):
        total_late = cust['paid_late']
        if total_late > 1:
            old = cust['avg_payment_delay']
            cust['avg_payment_delay'] = ((old * (total_late - 1)) + new_delay) / total_late
        else:
            cust['avg_payment_delay'] = new_delay

    def _extract_risk_factors(self, cust: Dict, row):
        rf = cust.get('risk_factors', [])

        if 'profit margin trend' in row.index:
            if 'declining' in str(row['profit margin trend']).lower():
                if 'Declining profit margin' not in rf:
                    rf.append('Declining profit margin')

        if 'cash flow status' in row.index:
            if 'negative' in str(row['cash flow status']).lower():
                if 'Negative cash flow' not in rf:
                    rf.append('Negative cash flow')

        if 'credit hold history' in row.index and row['credit hold history']:
            if 'Credit hold history' not in rf:
                rf.append('Credit hold history')

        if 'disputed' in row.index:
            if row['disputed'] in (1, '1', 'Yes', 'yes', True):
                if 'Disputed invoice' not in rf:
                    rf.append('Disputed invoice')

        cust['risk_factors'] = rf[:10]

    # ════════════════════════════════════════════════════════
    #  PUBLIC API
    # ════════════════════════════════════════════════════════

    def load_customer_data(self, df: pd.DataFrame, source: str = "upload"):
        cols_lower = [c.lower().strip() for c in df.columns]

        if 'dayslate' in cols_lower or 'daystosettle' in cols_lower or 'countrycode' in cols_lower:
            self._load_ibm_dataset(df, source)
        elif 'invoice_id' in cols_lower or 'late_payment' in cols_lower or 'late payment' in cols_lower:
            self._load_dataset_csv(df, source)
        else:
            for _, row in df.iterrows():
                name = self._extract_customer_name(row)
                if name:
                    self._load_customer_history_csv(pd.DataFrame([row]), source)

        print(f"Loaded data from {source}")

    def find_customer_match(self, name: str) -> Optional[Dict]:
        if not name or name.lower() in ('n/a', 'unknown', 'none', ''):
            return None

        search = name.strip().lower()

        # Exact
        for n, d in self.customer_history.items():
            if n.lower() == search:
                return d

        # Partial
        for n, d in self.customer_history.items():
            if search in n.lower() or n.lower() in search:
                return d

        # Cleaned
        cs = re.sub(r'[^a-z0-9]', '', search)
        for n, d in self.customer_history.items():
            cn = re.sub(r'[^a-z0-9]', '', n.lower())
            if cs in cn or cn in cs:
                return d

        return None

    def calculate_payment_reliability(self, data: Dict) -> float:
        if not data:
            return 0.5
        total = data.get('total_invoices', 0)
        if total == 0:
            return 0.5

        rel = data.get('paid_on_time', 0) / total

        not_paid = data.get('not_paid', 0)
        if not_paid > 0:
            rel *= max(0.5, 1 - (not_paid / total * 0.5))

        delay = data.get('avg_payment_delay', 0)
        if delay > 30:
            rel *= 0.7
        elif delay > 15:
            rel *= 0.85
        elif delay > 7:
            rel *= 0.95

        return max(0.1, min(1.0, rel))

    def calculate_payment_risk(self, data: Dict, invoice_amount: float = 0) -> Dict:
        rel = self.calculate_payment_reliability(data)
        risk = 1 - rel

        if invoice_amount > 0:
            total = data.get('total_amount', 0)
            count = max(data.get('total_invoices', 1), 1)
            avg = total / count
            if invoice_amount > avg * 2:
                risk *= 1.2
            elif invoice_amount > avg * 1.5:
                risk *= 1.1

        rf = data.get('risk_factors', [])
        risk *= (1 + len(rf) * 0.1)
        risk = min(1.0, max(0.0, risk))

        if risk > 0.7:
            level = "High"
        elif risk > 0.4:
            level = "Medium"
        else:
            level = "Low"

        confidence = min(1.0, data.get('total_invoices', 0) / 10)

        return {
            'risk_score': round(risk, 3),
            'risk_level': level,
            'payment_reliability': round(rel, 3),
            'confidence': round(confidence, 2),
            'historical_data': {
                'total_invoices': data.get('total_invoices', 0),
                'paid_on_time': data.get('paid_on_time', 0),
                'paid_late': data.get('paid_late', 0),
                'not_paid': data.get('not_paid', 0),
                'avg_delay': round(data.get('avg_payment_delay', 0), 1),
                'total_amount': round(data.get('total_amount', 0), 2)
            },
            'risk_factors': rf[:5]
        }

    def get_all_customers(self) -> List[Dict]:
        customers = []
        for name, data in self.customer_history.items():
            c = data.copy()
            c['payment_reliability'] = self.calculate_payment_reliability(data)
            customers.append(c)
        return sorted(customers, key=lambda x: x.get('payment_reliability', 0), reverse=True)

    def get_high_risk_customers(self, threshold: float = 0.6) -> List[Dict]:
        result = []
        for data in self.customer_history.values():
            rel = self.calculate_payment_reliability(data)
            if rel < threshold:
                c = data.copy()
                c['payment_reliability'] = rel
                c['risk_level'] = "High" if rel < 0.4 else "Medium"
                result.append(c)
        return sorted(result, key=lambda x: x['payment_reliability'])

    def get_stats(self) -> Dict:
        if not self.customer_history:
            return {'total_customers': 0, 'total_records': 0, 'high_risk_count': 0,
                    'avg_reliability': 0, 'total_amount': 0}

        rels = [self.calculate_payment_reliability(c) for c in self.customer_history.values()]
        return {
            'total_customers': len(self.customer_history),
            'total_records': len(self.dataset_records),
            'high_risk_count': len(self.get_high_risk_customers()),
            'avg_reliability': round(np.mean(rels), 2) if rels else 0,
            'total_amount': round(sum(c.get('total_amount', 0) for c in self.customer_history.values()), 2)
        }