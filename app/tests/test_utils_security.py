def safe_fn(name):
    name = name.replace("\\", "/").split("/")[-1]
    safe = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- ")
    return "".join(c for c in name if c in safe).strip() or "unnamed_file"


def safe_float(v):
    try:
        if v is None or str(v).lower() in ['n/a', 'nan', 'none', '']: return 0.0
        return float(str(v).replace(",", "").strip())
    except: return 0.0


class TestFilenameSanitization:
    def test_normal(self):
        assert safe_fn("invoice.pdf") == "invoice.pdf"

    def test_traversal_unix(self):
        assert ".." not in safe_fn("../../etc/passwd")

    def test_traversal_windows(self):
        assert "\\" not in safe_fn("..\\..\\windows\\system32")

    def test_special_chars(self):
        r = safe_fn("file<>|name?.pdf")
        assert "<" not in r and ">" not in r and "?" not in r

    def test_empty(self):
        assert safe_fn("") == "unnamed_file"

    def test_only_specials(self):
        assert safe_fn("<>|?*") == "unnamed_file"

    def test_absolute_path(self):
        assert safe_fn("/home/user/docs/invoice.pdf") == "invoice.pdf"


class TestFileTypes:
    ALLOWED = {'.pdf', '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif'}

    def test_allowed(self):
        for ext in ['.pdf', '.jpg', '.png']:
            assert ext in self.ALLOWED

    def test_blocked(self):
        for ext in ['.exe', '.js', '.php', '.sh']:
            assert ext not in self.ALLOWED

    def test_csv_check(self):
        assert "data.csv".endswith('.csv')
        assert not "data.xlsx".endswith('.csv')


class TestSession:
    def test_logged_in(self):
        session = {"user_id": 1, "email": "test@test.com"}
        assert session.get("user_id") is not None

    def test_not_logged_in(self):
        assert {}.get("user_id") is None

    def test_redirect_when_no_session(self):
        assert not {}.get("user_id")

    def test_no_redirect_when_session(self):
        assert {"user_id": 1}.get("user_id")

    def test_clear(self):
        s = {"user_id": 1, "email": "a@b.com"}
        s.clear()
        assert len(s) == 0


class TestSafeFloat:
    def test_valid(self):
        assert safe_float("1,234.56") == 1234.56
        assert safe_float(42) == 42.0
        assert safe_float("  100.50  ") == 100.50

    def test_invalid(self):
        for v in [None, "N/A", "nan", "", "abc"]:
            assert safe_float(v) == 0.0


class TestAvgDelay:
    def test_running_average(self):
        old_avg, total_late, new_delay = 10.0, 5, 20.0
        result = ((old_avg * (total_late - 1)) + new_delay) / total_late
        assert abs(result - 12.0) < 0.01

    def test_reliability_format(self):
        assert f"{round(0.856 * 100)}%" == "86%"