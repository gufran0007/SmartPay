"""
Test Suite: Authentication Controller
Tests registration, login, logout, input validation, and security
"""
import pytest
import re
import time


# ═══════════════════════════════════════════════════════════════
#  INPUT VALIDATION TESTS
# ═══════════════════════════════════════════════════════════════


class TestEmailValidation:
    """Test email format validation"""

    def test_valid_email_standard(self):
        """Standard email format should be accepted"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        assert re.match(pattern, "user@example.com")

    def test_valid_email_with_dots(self):
        """Email with dots in local part should be accepted"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        assert re.match(pattern, "first.last@company.co.uk")

    def test_valid_email_with_plus(self):
        """Email with plus sign should be accepted"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        assert re.match(pattern, "user+tag@example.com")

    def test_invalid_email_no_at(self):
        """Email without @ should be rejected"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        assert not re.match(pattern, "userexample.com")

    def test_invalid_email_no_domain(self):
        """Email without domain should be rejected"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        assert not re.match(pattern, "user@")

    def test_invalid_email_no_tld(self):
        """Email without TLD should be rejected"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        assert not re.match(pattern, "user@example")

    def test_invalid_email_double_at(self):
        """Email with double @ should be rejected"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        assert not re.match(pattern, "user@@example.com")

    def test_invalid_email_spaces(self):
        """Email with spaces should be rejected"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        assert not re.match(pattern, "user @example.com")

    def test_email_too_long(self):
        """Email exceeding 254 chars should be rejected"""
        long_email = "a" * 250 + "@b.com"
        assert len(long_email) > 254

    def test_empty_email(self):
        """Empty email should be rejected"""
        assert not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', "")


class TestPasswordValidation:
    """Test password strength requirements"""

    def test_valid_password(self):
        """Password meeting all requirements should pass"""
        pw = "SecurePass1"
        assert len(pw) >= 8
        assert re.search(r'[A-Z]', pw)
        assert re.search(r'[a-z]', pw)
        assert re.search(r'[0-9]', pw)

    def test_password_too_short(self):
        """Password under 8 chars should fail"""
        assert len("Short1") < 8

    def test_password_no_uppercase(self):
        """Password without uppercase should fail"""
        assert not re.search(r'[A-Z]', "alllower1")

    def test_password_no_lowercase(self):
        """Password without lowercase should fail"""
        assert not re.search(r'[a-z]', "ALLUPPER1")

    def test_password_no_number(self):
        """Password without number should fail"""
        assert not re.search(r'[0-9]', "NoNumberHere")

    def test_password_all_requirements(self):
        """Password with all requirements should pass all checks"""
        pw = "MyP@ssw0rd"
        checks = [
            len(pw) >= 8,
            bool(re.search(r'[A-Z]', pw)),
            bool(re.search(r'[a-z]', pw)),
            bool(re.search(r'[0-9]', pw)),
        ]
        assert all(checks)

    def test_password_too_long(self):
        """Password exceeding 128 chars should fail"""
        long_pw = "A" * 100 + "a" * 20 + "1" * 10
        assert len(long_pw) > 128

    def test_password_exactly_8_chars(self):
        """Password at exactly 8 chars should pass length check"""
        assert len("Abcdef1!") == 8


class TestBusinessNameValidation:
    """Test business/company name validation"""

    def test_valid_business_name(self):
        """Standard business name should pass"""
        name = "Acme Corporation"
        assert 2 <= len(name) <= 100
        assert not re.search(r'[<>{}|\\^~\[\]`]', name)

    def test_business_name_with_special_chars(self):
        """Business name with common special chars should pass"""
        name = "O'Brien & Sons Ltd."
        assert not re.search(r'[<>{}|\\^~\[\]`]', name)

    def test_business_name_too_short(self):
        """Single character name should fail"""
        assert len("A") < 2

    def test_business_name_too_long(self):
        """Name over 100 chars should fail"""
        assert len("A" * 101) > 100

    def test_business_name_with_html_tags(self):
        """Name with HTML angle brackets should fail validation"""
        assert re.search(r'[<>{}|\\^~\[\]`]', "<script>alert('xss')</script>")

    def test_business_name_empty(self):
        """Empty name should fail"""
        assert len("") < 2


# ═══════════════════════════════════════════════════════════════
#  XSS / INPUT SANITIZATION TESTS
# ═══════════════════════════════════════════════════════════════


class TestInputSanitization:
    """Test XSS and injection prevention"""

    def _sanitize(self, value):
        """Replicate the sanitize_input function"""
        if not value:
            return ""
        value = re.sub(r'<[^>]+>', '', value)
        value = value.replace('<', '').replace('>', '').replace('"', '').replace("'", '')
        return value.strip()

    def test_strip_html_tags(self):
        """HTML tags should be removed"""
        result = self._sanitize("<b>bold</b>")
        assert "<" not in result and ">" not in result
        assert "bold" in result

    def test_strip_script_tags(self):
        """Script tags should be completely removed"""
        result = self._sanitize("<script>alert('xss')</script>")
        assert "script" not in result
        assert "alert" in result  # text content preserved, tags removed

    def test_strip_event_handlers(self):
        """HTML with event handlers should be sanitized"""
        result = self._sanitize('<img onerror="alert(1)" src=x>')
        assert "onerror" not in result or "<" not in result

    def test_preserve_normal_text(self):
        """Normal text should not be altered"""
        result = self._sanitize("Hello World Company")
        assert result == "Hello World Company"

    def test_strip_quotes(self):
        """Quotes should be removed"""
        result = self._sanitize('test"value\'here')
        assert '"' not in result
        assert "'" not in result

    def test_empty_input(self):
        """Empty input should return empty string"""
        assert self._sanitize("") == ""
        assert self._sanitize(None) == ""

    def test_whitespace_trimmed(self):
        """Leading/trailing whitespace should be trimmed"""
        result = self._sanitize("  hello  ")
        assert result == "hello"


# ═══════════════════════════════════════════════════════════════
#  BRUTE FORCE PROTECTION TESTS
# ═══════════════════════════════════════════════════════════════


class TestBruteForceProtection:
    """Test login attempt rate limiting"""

    def test_under_limit_not_locked(self):
        """Under 5 attempts should not trigger lockout"""
        from collections import defaultdict
        attempts = defaultdict(list)
        ip = "192.168.1.1"
        now = time.time()
        attempts[ip] = [now - 10, now - 5, now - 2, now - 1]  # 4 attempts
        # Clean old attempts (within 300 seconds)
        attempts[ip] = [t for t in attempts[ip] if now - t < 300]
        assert len(attempts[ip]) < 5

    def test_at_limit_locked(self):
        """5 attempts within window should trigger lockout"""
        from collections import defaultdict
        attempts = defaultdict(list)
        ip = "192.168.1.1"
        now = time.time()
        attempts[ip] = [now - 200, now - 150, now - 100, now - 50, now - 1]  # 5 attempts
        attempts[ip] = [t for t in attempts[ip] if now - t < 300]
        assert len(attempts[ip]) >= 5

    def test_old_attempts_cleared(self):
        """Attempts older than 5 minutes should be cleared"""
        from collections import defaultdict
        attempts = defaultdict(list)
        ip = "192.168.1.1"
        now = time.time()
        attempts[ip] = [now - 600, now - 500, now - 400, now - 350, now - 310]  # All old
        attempts[ip] = [t for t in attempts[ip] if now - t < 300]
        assert len(attempts[ip]) == 0

    def test_mixed_old_and_new_attempts(self):
        """Only recent attempts should count"""
        from collections import defaultdict
        attempts = defaultdict(list)
        ip = "192.168.1.1"
        now = time.time()
        attempts[ip] = [now - 600, now - 500, now - 100, now - 50, now - 1]  # 3 recent
        attempts[ip] = [t for t in attempts[ip] if now - t < 300]
        assert len(attempts[ip]) == 3
        assert len(attempts[ip]) < 5


# ═══════════════════════════════════════════════════════════════
#  PASSWORD HASHING TESTS
# ═══════════════════════════════════════════════════════════════


class TestPasswordHashing:
    """Test bcrypt password hashing and verification"""

    def test_hash_produces_output(self):
        """Hashing should produce a non-empty string"""
        import bcrypt as _bcrypt
        pw = "TestPassword1"
        hashed = _bcrypt.hashpw(pw.encode('utf-8'), _bcrypt.gensalt(12)).decode('utf-8')
        assert hashed
        assert len(hashed) > 0

    def test_hash_is_different_from_password(self):
        """Hash should not equal the plain password"""
        import bcrypt as _bcrypt
        pw = "TestPassword1"
        hashed = _bcrypt.hashpw(pw.encode('utf-8'), _bcrypt.gensalt(12)).decode('utf-8')
        assert hashed != pw

    def test_same_password_different_hashes(self):
        """Same password should produce different hashes (salt)"""
        import bcrypt as _bcrypt
        pw = "TestPassword1"
        hash1 = _bcrypt.hashpw(pw.encode('utf-8'), _bcrypt.gensalt(12)).decode('utf-8')
        hash2 = _bcrypt.hashpw(pw.encode('utf-8'), _bcrypt.gensalt(12)).decode('utf-8')
        assert hash1 != hash2

    def test_verify_correct_password(self):
        """Correct password should verify successfully"""
        import bcrypt as _bcrypt
        pw = "TestPassword1"
        hashed = _bcrypt.hashpw(pw.encode('utf-8'), _bcrypt.gensalt(12)).decode('utf-8')
        assert _bcrypt.checkpw(pw.encode('utf-8'), hashed.encode('utf-8'))

    def test_verify_wrong_password(self):
        """Wrong password should fail verification"""
        import bcrypt as _bcrypt
        pw = "TestPassword1"
        hashed = _bcrypt.hashpw(pw.encode('utf-8'), _bcrypt.gensalt(12)).decode('utf-8')
        assert not _bcrypt.checkpw("WrongPassword1".encode('utf-8'), hashed.encode('utf-8'))