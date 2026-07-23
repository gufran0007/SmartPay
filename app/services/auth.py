"""
Shared authentication/tenancy dependency for Smart Pay routes.

Replaces the old pattern of every controller re-checking
`request.session.get("user_id")` by hand (easy to forget, and several
routes did) with a single FastAPI dependency: `require_account`.

The actual session-to-account resolution and query-layer scoping happens
once per request in `AccountScopeMiddleware` (see app.services.tenancy
for why this has to be middleware rather than done inside the
dependency itself). `require_account` just reads what the middleware
already put on `request.state` and turns "not logged in" into a redirect
to /login via an exception, so routes never need to repeat that check.
"""
from dataclasses import dataclass
from typing import Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.models.database import SessionLocal, User, Account
from app.services.tenancy import _current_account_id


class RequireLoginRedirect(Exception):
    """No valid session. Caught by an exception handler in main.py which
    turns this into a redirect to /login."""


@dataclass
class CurrentUser:
    user_id: int
    email: str
    account_id: int
    account_name: str


class AccountScopeMiddleware(BaseHTTPMiddleware):
    """Resolves the session cookie to a CurrentUser once per request,
    stashes it on request.state, and sets query-layer tenant scoping for
    the lifetime of the request."""

    async def dispatch(self, request: Request, call_next):
        current = None
        user_id = request.session.get("user_id")
        if user_id:
            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_id).first()
                if user:
                    account = db.query(Account).filter(Account.id == user.account_id).first()
                    current = CurrentUser(
                        user_id=user.id,
                        email=user.email,
                        account_id=user.account_id,
                        account_name=account.name if account else "",
                    )
            finally:
                db.close()

        request.state.current_user = current
        token = _current_account_id.set(current.account_id if current else None)
        try:
            return await call_next(request)
        finally:
            _current_account_id.reset(token)


def require_account(request: Request) -> CurrentUser:
    current = getattr(request.state, "current_user", None)
    if current is None:
        raise RequireLoginRedirect()
    return current


def get_current_user_optional(request: Request) -> Optional[dict]:
    """For the public/guest landing page only: tells the template whether
    someone is logged in without forcing a redirect."""
    current = getattr(request.state, "current_user", None)
    if current is None:
        return None
    return {"user_id": current.user_id, "email": current.email}
