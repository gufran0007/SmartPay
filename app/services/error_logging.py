"""
Central exception logging so a caught-and-swallowed error (or the global
500 handler) always leaves a traceback behind, tagged consistently so it's
one grep away in `docker compose logs`.

Deliberately does not use `logging.basicConfig` (a silent no-op if the root
logger already has handlers attached -- order-of-import dependent and easy
to lose) and does not rely on uvicorn's incidental re-raise-after-response
being printed to the console. This module owns its own logger with its own
handler, so it emits regardless of how uvicorn/gunicorn configure theirs,
and regardless of import order.
"""
import logging
import sys

LOG_TAG = "SMARTPAY_ERROR"

logger = logging.getLogger("smartpay")
logger.setLevel(logging.INFO)

if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_handler)


def log_exception(request, where: str, **context) -> None:
    """Log the currently-handled exception with request context.

    Must be called from inside an `except` block (or an exception handler
    still nested in one, e.g. Starlette's ServerErrorMiddleware) -- it reads
    the active exception off `sys.exc_info()` via `logger.exception()`.

    `where` is a short label for the code path (e.g. "upload_csv"). Extra
    `context` kwargs (filename=, import_id=, ...) are appended to the line.
    Always starts with LOG_TAG so every occurrence is one `grep` away.
    """
    current_user = getattr(request.state, "current_user", None)
    parts = [
        LOG_TAG,
        f"where={where}",
        f"method={request.method}",
        f"path={request.url.path}",
        f"account_id={getattr(current_user, 'account_id', None)}",
        f"user={getattr(current_user, 'email', None)}",
    ]
    parts.extend(f"{key}={value}" for key, value in context.items())
    logger.exception(" ".join(parts))
