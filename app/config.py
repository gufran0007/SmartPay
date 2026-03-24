from pathlib import Path

# Absolute project root (one level above /app)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_DIR = PROJECT_ROOT / "uploads"

# Make sure uploads directory exists
UPLOAD_DIR.mkdir(exist_ok=True)
