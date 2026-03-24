# Smart Pay - AI-Powered Invoice Management & Payment Prediction

**Final Year Project** | Ghufran Aslam (K00290558)  
BSc in Software Development, Technological University of the Shannon

---

## What It Does

Smart Pay is a web application that helps businesses predict which invoices are likely to be paid late. Upload a PDF or image invoice, the system extracts the data using OCR, and a machine learning model flags invoices that are at risk of going overdue.

### Features

- **OCR Invoice Extraction** - Upload PDF or image invoices. PyMuPDF and Tesseract extract invoice number, customer name, email, amounts, dates, currency, company name, and VAT ID automatically.
- **ML Payment Prediction** - Random Forest classifier trained on real payment data predicts late payment probability with a confidence score and explanation of contributing factors.
- **Customer Risk Profiles** - Payment reliability scoring, fuzzy name matching, and risk assessment based on historical payment behaviour.
- **Invoice Status Tracking** - Invoices move through Pending, Paid, Overdue, and Disputed states. Overdue detection runs automatically.
- **Interactive Dashboard** - Chart.js graphs showing risk distribution and payment behaviour. Different views for guests (landing page) and logged-in users (data dashboard).
- **Authentication & Security** - bcrypt password hashing, session-based auth, brute force protection, XSS sanitisation, file upload validation.
- **109 Automated Tests** - Pytest test suite covering auth, extraction, ML, CSV processing, and security. Runs in under 3 seconds with no external dependencies.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (Python 3.13) |
| Database | SQLite + SQLAlchemy ORM |
| ML | scikit-learn (Random Forest, 7 features) |
| OCR | PyMuPDF + Tesseract |
| Frontend | Jinja2 + TailwindCSS + DaisyUI |
| Charts | Chart.js |
| Auth | bcrypt + Starlette SessionMiddleware |
| Testing | Pytest (109 tests) |
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
│   │   ├── csv_controller.py        # CSV import, customer views
│   │   └── prediction_controller.py # ML training, prediction, analytics
│   │
│   ├── services/
│   │   ├── csv_service.py           # Customer data, reliability scoring, fuzzy matching
│   │   ├── ml_service.py            # Random Forest training & prediction
│   │   ├── auth_service.py          # Password hashing & verification
│   │   ├── paths.py                 # Centralised path configuration
│   │   └── state.py                 # Singleton service instances
│   │
│   ├── models/
│   │   ├── database.py              # SQLAlchemy models & DB setup
│   │   └── payment_predictor.pkl    # Trained ML model (generated after training)
│   │
│   ├── templates/                   # 20+ Jinja2 HTML templates
│   ├── static/                      # CSS files
│   ├── data/                        # IBM training dataset (CSV)
│   ├── tests/                       # 5 test files, 109 tests
│   └── uploads/                     # Uploaded invoice files
│
└── venv/                            # Virtual environment (not committed)
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

1. Register an account at `/register`
2. Upload the IBM dataset from `app/data/` at `/upload-csv`
3. Train the ML model at `/ml/train`
4. Upload an invoice (PDF or image) at `/upload`
5. Click **Predict** on any invoice to see the risk score

---

## Running Tests

```bash
# Run all 109 tests
pytest

# Verbose output
pytest -v

# With coverage report
pytest --cov=app --cov-report=term-missing

# Run a specific module
pytest app/tests/test_auth.py -v

# Run a specific test class
pytest app/tests/test_ml_service.py::TestModel -v
```

All tests are self-contained. No database, server, or network required.

| Module | Tests | Covers |
|--------|-------|--------|
| test_auth.py | 24 | Email validation, password strength, XSS sanitisation, brute force, bcrypt |
| test_csv_service.py | 24 | Reliability scoring, risk assessment, fuzzy matching, CSV loading |
| test_invoice_extraction.py | 26 | Invoice number regex, amount parsing (EU/US), currency, dates, full pipeline |
| test_ml_service.py | 16 | Feature engineering, model training, accuracy threshold, prediction blending |
| test_utils_security.py | 19 | Filename sanitisation, path traversal, file type whitelist, session helpers |

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

The payment prediction model uses a **Random Forest Classifier** trained on the [IBM Finance Factoring Late Payment Histories](https://www.kaggle.com/datasets/hhenry/finance-factoring-ibm-late-payment-histories) dataset (2,466 real accounts receivable records).

**7 Features:**
1. Normalised invoice amount
2. Payment reliability (on-time ratio)
3. Normalised average delay
4. Settlement speed
5. Late payment ratio
6. Disputed flag
7. Risk factor count

**Prediction Blending:**
- Known customers: 60% ML model + 40% historical risk score
- Unknown customers: 45% floor + due date proximity + amount-based adjustments
- Overdue invoices: +1% per day past due (capped at +40%)

---

## API Routes

| Route | Method | Auth | Description |
|-------|--------|------|-------------|
| `/dashboard` | GET | No | Landing page (guest) or data dashboard (logged in) |
| `/register` | GET/POST | No | User registration |
| `/login` | GET/POST | No | User login |
| `/logout` | GET | Yes | Clear session |
| `/upload` | GET/POST | Yes | Upload invoice (PDF/image) |
| `/upload-csv` | GET/POST | Yes | Upload CSV dataset |
| `/view-data` | GET | Yes | All invoices with status filters |
| `/view-invoice/{id}` | GET | Yes | Single invoice detail |
| `/edit-invoice/{id}` | GET/POST | Yes | Edit extracted data |
| `/update-status/{id}` | POST | Yes | Change invoice status |
| `/customers` | GET | Yes | Customer list with risk scores |
| `/customer-insights/{name}` | GET | Yes | Detailed customer analysis |
| `/ml/train` | GET | Yes | Train ML model |
| `/ml/predict/{id}` | GET | Yes | Run prediction |
| `/ml/analytics` | GET | Yes | ML analytics dashboard |
| `/ml/explain/{id}` | GET | Yes | Prediction explanation |

---

## Environment Variables

Create a `.env` file in the project root (or in `app/services/`):

```
SECRET_KEY=your-secret-key-here
GMAIL_USER=your-email@gmail.com
GMAIL_APP_PASSWORD=your-app-password
```

`SECRET_KEY` is used to sign session cookies. Gmail credentials are optional and only needed for the email reminder feature.

---

## Dataset

This project uses the **IBM Finance Factoring Late Payment Histories** dataset:

> Henry, H. (2019). Finance Factoring - IBM Late Payment Histories. Kaggle.  
> https://www.kaggle.com/datasets/hhenry/finance-factoring-ibm-late-payment-histories

2,466 real accounts receivable records. Cited in Malfatti et al. (2019) and Nalepa (2022).

**Note:** Customer IDs in the dataset are anonymised. The application maps them to readable company names for display purposes. The underlying data is unchanged.

---

## License

This project was developed as a Final Year Project for BSc in Software Development at TUS.

---

**Built by Ghufran Aslam (K00290558)**