"""
Authentication Controller for Smart Pay
Handles user registration and login
"""
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
import bcrypt as _bcrypt

class _bc:
    @staticmethod
    def hash(password):
        return _bcrypt.hashpw(password.encode('utf-8'), _bcrypt.gensalt(12)).decode('utf-8')
    @staticmethod
    def verify(password, hashed):
        try:
            return _bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
        except Exception:
            return False

bcrypt = _bc()

from app.models.database import SessionLocal, User, Account

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def render(request: Request, template: str, **context):
    """Render a template with common context"""
    return templates.TemplateResponse(template, {"request": request, **context})


@router.get("/register")
def register_page(request: Request):
    """Display registration page"""
    return render(request, "register.html")


@router.post("/register")
def register_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...)
):
    """Handle user registration"""
    db = SessionLocal()
    try:
        # Check if email already exists
        existing = db.query(User).filter_by(email=email.lower().strip()).first()
        if existing:
            return render(request, "register.html", error="Email already registered")
        
        # Validate email format
        if '@' not in email or '.' not in email:
            return render(request, "register.html", error="Invalid email format")
        
        # Validate password length
        if len(password) < 6:
            return render(request, "register.html", error="Password must be at least 6 characters")
        
        # Each account starts life with exactly one user: the person
        # who registered. Additional users per account can come later
        # without another migration, since the FK is already in place.
        account = Account(name=f"{email.lower().strip()}'s Account")
        db.add(account)
        db.flush()  # assigns account.id without a separate commit

        user = User(
            account_id=account.id,
            email=email.lower().strip(),
            password=bcrypt.hash(password)
        )
        db.add(user)
        db.commit()
        
        return RedirectResponse("/login?registered=1", status_code=303)
        
    except Exception as e:
        db.rollback()
        print(f"Registration error: {e}")
        return render(request, "register.html", error="Registration failed. Please try again.")
    finally:
        db.close()


@router.get("/login")
def login_page(request: Request):
    """Display login page"""
    registered = request.query_params.get("registered")
    message = "Account created successfully! Please log in." if registered else None
    return render(request, "login.html", message=message)


@router.post("/login")
def login_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...)
):
    """Handle user login"""
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(email=email.lower().strip()).first()
        
        if user and bcrypt.verify(password, user.password):
            # ✅ ADDED: Save user info in session cookie
            request.session["user_id"] = user.id
            request.session["email"] = user.email
            # Successful login - redirect to dashboard
            return RedirectResponse("/dashboard", status_code=303)
        
        return render(request, "login.html", error="Invalid email or password")
        
    except Exception as e:
        print(f"Login error: {e}")
        return render(request, "login.html", error="Login failed. Please try again.")
    finally:
        db.close()


@router.get("/logout")
def logout(request: Request):
    """Handle logout"""
    # ✅ ADDED: Clear session before redirecting
    request.session.clear()
    return RedirectResponse("/login", status_code=303)