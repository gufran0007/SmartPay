"""
Date parsing for invoice imports, with DD/MM vs MM/DD disambiguation.

A bare numeric date like "03/04/2024" is genuinely ambiguous without more
context. The approach here: look at every value in the same column — if any
of them has a first component greater than 12, the column can't be MM/DD (no
13th month), so it's day-first; if any has a second component greater than
12, it can't be DD/MM, so it's month-first. One unambiguous value in the
column settles it for the whole column, since a real export is consistent
within itself. With no disambiguating evidence anywhere in the column, this
defaults to day-first (international format) — documented here since it's
a real assumption, not a derived fact.
"""
import re
from datetime import date, datetime
from typing import Optional

_NUMERIC_DATE_RE = re.compile(r'^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$')
_ISO_DATE_RE = re.compile(r'^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})$')

_TEXTUAL_FORMATS = [
    "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y",
    "%b %d %Y", "%d %b, %Y", "%d-%b-%Y", "%d-%B-%Y",
]

_BLANK_VALUES = {"", "n/a", "nan", "none", "nat"}


def _normalize_year(raw: str) -> int:
    y = int(raw)
    if y < 100:
        return 2000 + y if y < 70 else 1900 + y
    return y


def infer_day_first(raw_values) -> bool:
    """Inspect a column's raw values and decide whether ambiguous numeric
    dates in it are day-first. Defaults to day-first with no evidence."""
    for v in raw_values:
        if v is None:
            continue
        s = str(v).strip()
        m = _NUMERIC_DATE_RE.match(s)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12:
            return True
        if b > 12:
            return False
    return True


def parse_canonical_date(raw, day_first: bool = True) -> Optional[date]:
    """Parse one date value. `day_first` should come from infer_day_first()
    run over the whole column this value belongs to."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() in _BLANK_VALUES:
        return None

    m = _ISO_DATE_RE.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d)
        except ValueError:
            return None

    m = _NUMERIC_DATE_RE.match(s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), _normalize_year(m.group(3))
        first, second = (a, b) if day_first else (b, a)
        try:
            return date(y, second, first)
        except ValueError:
            # The inferred order doesn't work for this particular value
            # (e.g. column default disagrees with this one row) — try the
            # other order before giving up.
            try:
                return date(y, first, second)
            except ValueError:
                return None

    for fmt in _TEXTUAL_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    return None
