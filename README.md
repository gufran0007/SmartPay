# Smart Pay - AI-Powered Invoice Management & Payment Prediction

**Final Year Project** | Ghufran Aslam (K00290558)  
BSc in Software Development, Technological University of the Shannon

---

## What It Does

Smart Pay is a multi-tenant web application that helps businesses predict which invoices are likely to be paid late. Each account uploads its own invoice history and gets its own prediction model — one account's data and models are never visible to, or trained on, another's.

### Features

- **Multi-tenant by design** - every account's invoices, uploads, and models are isolated at the query layer (not just by convention) — see `app/services/tenancy.py`.
- **OCR Invoice Extraction** - Upload PDF or image invoices. PyMuPDF and Tesseract extract invoice number, customer name, email, amounts, dates, currency, company name, and VAT ID automatically.
- **CSV/Excel invoice history import** - upload past invoices, an LLM proposes a column mapping to the canonical schema, you confirm or correct it, then deterministic (non-LLM) validation and import run — see `app/services/llm_mapping.py` and `app/services/invoice_import.py`.
- **Per-account ML Payment Prediction** - each account gets its own Random Forest, trained only on its own paid invoices, retrained after every import. Below a minimum invoice count it falls back to a transparent payment-terms heuristic instead — the UI always states which one produced a given prediction, and shows real held-out accuracy or says there isn't enough data yet. See `app/services/ml_service.py`.
- **Invoice Status Tracking** - Invoices move through Pending, Paid, Overdue, and Disputed states. Overdue detection runs automatically.
- **Authentication & Security** - bcrypt password hashing, session-based auth, brute force protection, XSS sanitisation, file upload validation.
- **Automated test suite** - Pytest, covering auth, extraction, ML, CSV/import processing, tenancy isolation, and security.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (Python 3.12+) |
| Database | SQLite + SQLAlchemy ORM, tenant-isolated via a query-layer hook |
| ML | scikit-learn (Random Forest, per-account) |
| Column mapping | Anthropic API (Claude), behind an interface with a fake for tests |
| OCR | PyMuPDF + Tesseract |
| Frontend | Jinja2 + TailwindCSS + DaisyUI |
| Auth | bcrypt + Starlette SessionMiddleware |
| Testing | Pytest |
| Container | Docker |

---

## Project Structure

```
smartpay/
├── main.py                          # FastAPI entry point
├── Dockerfile                       # Docker containerisation
├── requirements.txt                 # Python dependencies
├── pytest.ini                       # Test configuration
│
├── app/
│   ├── controllers/
│   │   ├── auth_controller.py       # Registration, login, logout
│   │   ├── invoice_controller.py    # Dashboard, upload, CRUD, status workflow
│   │   ├── csv_controller.py        # Legacy customer-history CSV upload, customer list views
│   │   ├── import_controller.py     # Canonical-schema invoice history import (upload -> mapping -> confirm)
│   │   └── prediction_controller.py # Per-account ML training, prediction, analytics
│   │
│   ├── services/
│   │   ├── tenancy.py               # Query-layer account isolation (SQLAlchemy hooks)
│   │   ├── auth.py                  # AccountScopeMiddleware + require_account dependency
│   │   ├── csv_service.py           # Legacy customer data / reliability scoring / fuzzy matching
│   │   ├── ml_service.py            # Per-account Random Forest training & prediction, heuristic fallback
│   │   ├── llm_mapping.py           # LLM column-mapping interface (+ Anthropic impl + fake for tests)
│   │   ├── invoice_import.py        # Deterministic validation/import engine
│   │   ├── date_parsing.py          # DD/MM vs MM/DD-aware date parsing
│   │   └── paths.py                 # Centralised, env-overridable path configuration
│   │
│   ├── models/
│   │   ├── database.py              # SQLAlchemy models, migrations, DB setup
│   │   └── payment_predictor_account_<id>.pkl  # Per-account trained models (gitignored)
│   │
│   ├── templates/                   # Jinja2 HTML templates
│   ├── static/                      # CSS files
│   ├── data/                        # (empty; no bundled dataset)
│   ├── tests/                       # Pytest suite
│   └── uploads/account_<id>/        # Per-account uploaded files
│
└── .venv/                           # Virtual environment (not committed)
```

---

## Getting Started

### Prerequisites

- Python 3.10+ (tested on 3.13)
- pip
- Tesseract OCR (optional, only needed for image invoice uploads)

### Installation

```bash
# Clone the repo
git clone <your-repo-url>
cd smartpay

# Create and activate virtual environment
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Run the Application

```bash
uvicorn main:app --reload
```

Open **http://127.0.0.1:8000** in your browser.

### First Time Setup

1. Register an account at `/register` (an Account is created automatically)
2. Import your invoice history (CSV/Excel) at `/import` — confirm the proposed column mapping before anything is imported
3. Once you have enough paid invoices, the model trains automatically (or trigger it manually at `/ml/train`)
4. Upload an invoice (PDF or image) at `/upload`
5. Click **Predict** on any invoice to see the risk score and which mode (ML or heuristic) produced it

---

## Running Tests

```bash
# Run the whole suite
pytest

# Verbose output
pytest -v

# With coverage report
pytest --cov=app --cov-report=term-missing

# Run a specific module
pytest app/tests/test_auth.py -v
```

All tests are self-contained: an isolated temp SQLite DB, uploads directory, and models
directory are configured in `app/tests/conftest.py` before anything imports the app, so
the suite never touches the real dev database or writes real model files into the source
tree. The LLM column-mapping provider is swapped for a fake via FastAPI's
`dependency_overrides` — no network calls in the suite.

| Module | Covers |
|--------|--------|
| test_auth.py | Email validation, password strength, XSS sanitisation, brute force, bcrypt |
| test_csv_service.py | Reliability scoring, risk assessment, fuzzy matching, CSV loading |
| test_invoice_extraction.py | Invoice number regex, amount parsing (EU/US), currency, dates, full pipeline |
| test_ml_service.py | Feature engineering, model training basics |
| test_ml_prediction.py | Per-account training: cold start, threshold boundary, retraining after import, unknown customer, model isolation between accounts |
| test_utils_security.py | Filename sanitisation, path traversal, file type whitelist, session helpers |
| test_tenancy.py | Login required on every data endpoint; account A can't read/modify account B's records |
| test_import.py | CSV/Excel import: multiple shapes, malformed/timeout LLM output, date-format disambiguation, duplicates, validation, error reports |

---

## Docker

```bash
# Build
docker build -t smartpay .

# Run
docker run -p 8000:8000 smartpay
```

---

## ML Model

Each account gets its own **Random Forest Classifier**, trained only on that account's own
paid invoices (never on another account's data, and never on a shared/reference dataset).

**Minimum training size:** 20 paid invoices. Below that, an 80/20 train/test split would
leave fewer than ~4-5 held-out examples — not enough to report a meaningful accuracy, and
too little data to fit a model that would actually beat a simple heuristic. Below the
threshold, predictions use a transparent payment-terms heuristic instead (shorter payment
terms and larger amounts are flagged as higher risk) — the UI always labels this as a
heuristic, never as ML.

**Features (per invoice):**
1. Invoice amount, normalised against the account's average paid invoice
2. Payment terms length (days between invoice date and due date)
3. This customer's own late-payment ratio, computed leaving the invoice itself out

**Retraining:** happens automatically after every confirmed invoice-history import (see
`/import`), and can be triggered manually at `/ml/train`.

---

## API Routes

| Route | Method | Auth | Description |
|-------|--------|------|-------------|
| `/dashboard` | GET | No | Landing page (guest) or data dashboard (logged in) |
| `/register` | GET/POST | No | User registration (creates an Account) |
| `/login` | GET/POST | No | User login |
| `/logout` | GET | Yes | Clear session |
| `/upload` | GET/POST | Yes | Upload invoice (PDF/image), OCR-extracted |
| `/import` | GET/POST | Yes | Upload CSV/Excel invoice history |
| `/import/{id}/review` | GET | Yes | Review/correct the LLM-proposed column mapping |
| `/import/{id}/confirm` | POST | Yes | Confirm mapping, validate, and import |
| `/import/{id}/errors` | GET | Yes | Download the row-level error report (CSV) |
| `/upload-csv` | GET/POST | Yes | Legacy customer-history CSV upload |
| `/view-data` | GET | Yes | All invoices with status filters |
| `/view-invoice/{id}` | GET | Yes | Single invoice detail |
| `/edit-invoice/{id}` | GET/POST | Yes | Edit extracted data |
| `/update-status/{id}` | POST | Yes | Change invoice status |
| `/customers` | GET | Yes | Customer list with risk scores |
| `/customer-insights/{name}` | GET | Yes | Detailed customer analysis |
| `/ml/train` | GET | Yes | (Re)train this account's model |
| `/ml/predict/{id}` | GET | Yes | Run a prediction (ML or heuristic) |
| `/ml/analytics` | GET | Yes | Prediction analytics + model status |
| `/ml/explain/{id}` | GET | Yes | Prediction explanation, states which mode produced it |

All routes above (except `/dashboard`, `/register`, `/login`) require an authenticated
session **and** are scoped to that session's account at the query layer — see
`app/services/tenancy.py`.

---

## Environment Variables

Create a `.env` file in the project root (or in `app/services/`):

```
SESSION_SECRET=your-secret-key-here
GMAIL_USER=your-email@gmail.com
GMAIL_APP_PASSWORD=your-app-password
ANTHROPIC_API_KEY=your-anthropic-api-key
```

`SESSION_SECRET` is used to sign session cookies (the app refuses to start without it — no
insecure default). Gmail credentials are only needed for the email reminder feature.
`ANTHROPIC_API_KEY` is only needed for the CSV/Excel import's column-mapping proposal
(`/import`) — never sent real customer data beyond headers and a small row sample, and
never used by the test suite.

---

## License

This project was developed as a Final Year Project for BSc in Software Development at TUS.

---

**Built by Ghufran Aslam (K00290558)**