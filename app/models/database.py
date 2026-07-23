"""
Database Models for Smart Pay
SQLAlchemy ORM models for accounts, users, invoices, and ML features
"""
import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Boolean, ForeignKey, Text, text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime
from pathlib import Path

from app.services.tenancy import TenantScopedSession, register_tenant_model, install_isolation

Base = declarative_base()


class Account(Base):
    """A tenant. Every customer's data lives under exactly one account."""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    users = relationship("User", back_populates="account")

    def __repr__(self):
        return f"<Account(id={self.id}, name={self.name})>"


class User(Base):
    """User model for authentication"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    email = Column(String(120), unique=True, nullable=False, index=True)
    password = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    account = relationship("Account", back_populates="users")

    def __repr__(self):
        return f"<User(id={self.id}, email={self.email})>"


@register_tenant_model
class Invoice(Base):
    """Invoice model for storing extracted invoice data"""
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)

    # Customer information
    customer_name = Column(String(255), default="N/A")
    customer_email = Column(String(255), default="N/A")
    customer_address = Column(Text, default="N/A")

    # Invoice details
    invoice_number = Column(String(255), default="N/A")
    customer_id = Column(String(100), nullable=True)  # external customer identifier, from CSV import
    amount = Column(String(50), default="N/A")  # Stored as string for flexibility
    currency = Column(String(10), default="N/A")
    payment_terms = Column(String(50), nullable=True)  # e.g. "Net 30", from CSV import

    # Dates
    date = Column(String(50), default="N/A")  # Invoice date
    due_date = Column(String(50), default="N/A")  # Payment due date
    paid_date = Column(String(50), nullable=True)  # set on import when the row has one

    # Company information
    company_name = Column(String(255), default="N/A")
    vat_id = Column(String(255), default="N/A")

    # File tracking
    filename = Column(String(255))
    status = Column(String(20), default="pending")

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship to ML features
    features = relationship("InvoiceFeatures", back_populates="invoice", uselist=False, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Invoice(id={self.id}, number={self.invoice_number}, customer={self.customer_name})>"

    @property
    def amount_float(self) -> float:
        """Convert amount string to float safely"""
        try:
            if self.amount and self.amount != "N/A":
                return float(self.amount.replace(",", ""))
            return 0.0
        except (ValueError, AttributeError):
            return 0.0

    @property
    def is_complete(self) -> bool:
        """Check if invoice has all essential fields"""
        return all([
            self.customer_name and self.customer_name != "N/A",
            self.amount and self.amount != "N/A",
            self.invoice_number and self.invoice_number != "N/A"
        ])


@register_tenant_model
class InvoiceFeatures(Base):
    """ML features and predictions for invoices"""
    __tablename__ = "invoice_features"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="CASCADE"), unique=True, nullable=False)

    # Relationship back to invoice
    invoice = relationship("Invoice", back_populates="features")

    # Business features (from CSV data or manual input)
    industry = Column(String(100), default="Unknown")
    country = Column(String(80), default="Unknown")
    company_size = Column(String(50), default="Unknown")  # Small/Medium/Large
    customer_type = Column(String(50), default="Unknown")  # New/Returning
    contract_type = Column(String(50), default="Unknown")  # One-time/Recurring

    # Financial indicators
    profit_margin_trend = Column(String(50), default="Unknown")  # Declining/Stable/Improving
    cash_flow_status = Column(String(50), default="Unknown")  # Positive/Negative
    recent_loan_applications = Column(Integer, default=0)
    credit_hold_history = Column(Boolean, default=False)

    # Benchmark data
    industry_payment_benchmark = Column(Float, default=0.5)
    economic_health_index = Column(Float, default=0.0)

    # Computed features
    amount = Column(Float, default=0.0)
    days_to_due = Column(Integer, default=0)
    past_due_ratio = Column(Float, default=0.0)

    # ML label (for training)
    late_payment = Column(Integer, nullable=True)  # 0 = on time, 1 = late

    # Prediction outputs
    predicted_probability = Column(Float, nullable=True)  # Probability of late payment
    predicted_label = Column(Integer, nullable=True)  # 0 = will pay on time, 1 = will be late

    # What produced this prediction, so the UI can always say so honestly
    prediction_mode = Column(String(20), nullable=True)  # "ml" or "heuristic"
    model_invoices_seen = Column(Integer, nullable=True)  # paid invoices the account's model was trained on
    model_held_out_accuracy = Column(Float, nullable=True)  # real held-out accuracy, null if not measurable

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<InvoiceFeatures(invoice_id={self.invoice_id}, predicted_label={self.predicted_label})>"


@register_tenant_model
class PendingImport(Base):
    """A CSV/Excel upload awaiting the user's column-mapping confirmation.

    Holds the LLM's proposed mapping plus enough of the source file (headers,
    a sample of rows) to render the review screen, and the path to the full
    file on disk so confirm-time import re-reads everything (not just the
    sample) and applies the user-confirmed mapping deterministically — the
    LLM is never involved in that second pass.
    """
    __tablename__ = "pending_imports"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)

    filename = Column(String(255), nullable=False)
    file_path = Column(String(1000), nullable=False)
    headers = Column(Text, nullable=False)  # JSON list[str]
    sample_rows = Column(Text, nullable=False)  # JSON list[dict]
    proposed_mapping = Column(Text, nullable=False)  # JSON: canonical_field -> {source_column, confidence}
    status = Column(String(20), default="pending_review")  # pending_review | confirmed | rejected

    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<PendingImport(id={self.id}, filename={self.filename}, status={self.status})>"


# Database configuration. Overridable so tests never touch the real dev DB.
DATABASE_PATH = Path("data/smartpay.db")
SQLALCHEMY_DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATABASE_PATH}")

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False  # Set to True for SQL debugging
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=TenantScopedSession)
install_isolation(SessionLocal)


LEGACY_ACCOUNT_NAME = "Legacy Data"


def _existing_tables(conn) -> set:
    rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    return {row[0] for row in rows}


def _table_columns(conn, table_name: str) -> set:
    rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    return {row[1] for row in rows}


def _migrate_schema():
    """Idempotent migration, additive only.

    Creates any wholly-new tables via create_all, then for pre-existing
    installs adds any missing columns. account_id additions are backfilled
    into a 'Legacy Data' account so nothing becomes silently invisible once
    query-layer isolation is turned on; the Stage 2 canonical-schema columns
    (customer_id, payment_terms, paid_date) are simple nullable additions
    with nothing to backfill.
    """
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        existing_tables = _existing_tables(conn)
        legacy_account_id = None

        def ensure_legacy_account():
            nonlocal legacy_account_id
            if legacy_account_id is not None:
                return legacy_account_id
            row = conn.execute(
                text("SELECT id FROM accounts WHERE name = :name"),
                {"name": LEGACY_ACCOUNT_NAME},
            ).fetchone()
            if row:
                legacy_account_id = row[0]
            else:
                conn.execute(
                    text("INSERT INTO accounts (name, created_at) VALUES (:name, :now)"),
                    {"name": LEGACY_ACCOUNT_NAME, "now": datetime.utcnow()},
                )
                row = conn.execute(
                    text("SELECT id FROM accounts WHERE name = :name"),
                    {"name": LEGACY_ACCOUNT_NAME},
                ).fetchone()
                legacy_account_id = row[0]
            return legacy_account_id

        for table in ("users", "invoices", "invoice_features"):
            if table not in existing_tables:
                continue
            columns = _table_columns(conn, table)
            if "account_id" in columns:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN account_id INTEGER REFERENCES accounts(id)"))
            acc_id = ensure_legacy_account()
            conn.execute(
                text(f"UPDATE {table} SET account_id = :acc WHERE account_id IS NULL"),
                {"acc": acc_id},
            )

        if "invoices" in existing_tables:
            columns = _table_columns(conn, "invoices")
            for column, ddl_type in (
                ("customer_id", "VARCHAR(100)"),
                ("payment_terms", "VARCHAR(50)"),
                ("paid_date", "VARCHAR(50)"),
            ):
                if column not in columns:
                    conn.execute(text(f"ALTER TABLE invoices ADD COLUMN {column} {ddl_type}"))

        if "invoice_features" in existing_tables:
            columns = _table_columns(conn, "invoice_features")
            for column, ddl_type in (
                ("prediction_mode", "VARCHAR(20)"),
                ("model_invoices_seen", "INTEGER"),
                ("model_held_out_accuracy", "FLOAT"),
            ):
                if column not in columns:
                    conn.execute(text(f"ALTER TABLE invoice_features ADD COLUMN {column} {ddl_type}"))


def init_db():
    """Initialize the database, creating and migrating tables as needed"""
    _migrate_schema()


def get_db():
    """Get a database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Create/migrate tables on import
init_db()
