"""Security settings derived from deployment environment variables."""
import os


def session_cookie_https_only() -> bool:
    """Use secure session cookies in production, with an explicit override.

    Local development defaults to HTTP so the login flow remains usable on
    localhost. The Docker image sets APP_ENV=production for HTTPS deployments.
    """
    override = os.environ.get("SESSION_HTTPS_ONLY")
    if override is not None:
        value = override.strip().casefold()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
        raise ValueError("SESSION_HTTPS_ONLY must be true or false")

    return os.environ.get("APP_ENV", "").strip().casefold() in {"prod", "production"}
