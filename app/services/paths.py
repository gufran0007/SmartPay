"""
Path configuration for Smart Pay
Centralized path management for uploads and data directories
"""
from pathlib import Path

# Project root directory (parent of 'app' folder)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Upload directory for invoice files
UPLOAD_DIR = PROJECT_ROOT / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Data directory for CSV files
DATA_DIR = PROJECT_ROOT / "app" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Models directory for ML models
MODELS_DIR = PROJECT_ROOT / "app" / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

# Static directory for CSS/JS files
STATIC_DIR = PROJECT_ROOT / "app" / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# Templates directory
TEMPLATES_DIR = PROJECT_ROOT / "app" / "templates"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


def get_upload_path(filename: str) -> Path:
    """Get full path for an uploaded file"""
    return UPLOAD_DIR / filename


def get_data_path(filename: str) -> Path:
    """Get full path for a data file"""
    return DATA_DIR / filename


def safe_filename(name: str) -> str:
    """Sanitize filename to prevent directory traversal attacks"""
    # Remove path separators
    name = name.replace("\\", "/").split("/")[-1]
    # Keep only safe characters
    safe_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- ")
    cleaned = "".join(c for c in name if c in safe_chars).strip()
    return cleaned or "unnamed_file"
