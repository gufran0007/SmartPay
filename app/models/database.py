"""
Database Models for Smart Pay
SQLAlchemy ORM models for users, invoices, and ML features
"""
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Boolean, ForeignKey, Text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime
from pathlib import Path

Base = declarative_base()


class User(Base):
    """User model for authentication"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(120), unique=True, nullable=False, index=True)
    password = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f"<User(id={self.id}, email={self.email})>"


class Invoice(Base):
    """Invoice model for storing extracted invoice data"""
    __tablename__ = "invoices"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Customer information
    customer_name = Column(String(255), default="N/A")
    customer_email = Column(String(255), default="N/A")
    customer_address = Column(Text, default="N/A")
    
    # Invoice details
    invoice_number = Column(String(255), default="N/A")
    amount = Column(String(50), default="N/A")  # Stored as string for flexibility
    currency = Column(String(10), default="N/A")
    
    # Dates
    date = Column(String(50), default="N/A")  # Invoice date
    due_date = Column(String(50), default="N/A")  # Payment due date
    
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


class InvoiceFeatures(Base):
    """ML features and predictions for invoices"""
    __tablename__ = "invoice_features"
    
    id = Column(Integer, primary_key=True, index=True)
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
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<InvoiceFeatures(invoice_id={self.invoice_id}, predicted_label={self.predicted_label})>"


# Database configuration
DATABASE_PATH = Path("smartpay.db")
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False  # Set to True for SQL debugging
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Initialize the database and create all tables"""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Get a database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Create tables on import
init_db()
