import re


def calc_reliability(d):
    if not d or d.get('total_invoices', 0) == 0: return 0.5
    total = d['total_invoices']
    rel = d.get('paid_on_time', 0) / total
    if d.get('not_paid', 0) > 0:
        rel *= max(0.5, 1 - (d['not_paid'] / total * 0.5))
    delay = d.get('avg_payment_delay', 0)
    if delay > 30: rel *= 0.7
    elif delay > 15: rel *= 0.85
    elif delay > 7: rel *= 0.95
    return max(0.1, min(1.0, rel))


def calc_risk(d, amount=0):
    rel = calc_reliability(d)
    risk = 1 - rel
    if amount > 0:
        avg = d.get('total_amount', 0) / max(d.get('total_invoices', 1), 1)
        if amount > avg * 2: risk *= 1.2
    risk *= (1 + len(d.get('risk_factors', [])) * 0.1)
    risk = min(1.0, max(0.0, risk))
    return risk, "High" if risk > 0.7 else "Medium" if risk > 0.4 else "Low"


def find_match(name, history):
    if not name or name.lower() in ['n/a', 'unknown', '']: return None
    s = name.strip().lower()
    for n, d in history.items():
        if n.lower() == s: return d
    for n, d in history.items():
        if s in n.lower() or n.lower() in s: return d
    cs = re.sub(r'[^a-z0-9]', '', s)
    for n, d in history.items():
        cn = re.sub(r'[^a-z0-9]', '', n.lower())
        if cs in cn or cn in cs: return d
    return None


def safe_float(v):
    try:
        if v is None or str(v).lower() in ['n/a', 'nan', 'none', '']: return 0.0
        return float(str(v).replace(",", "").strip())
    except: return 0.0


class TestReliability:
    def test_good_customer_high(self, good_customer):
        assert calc_reliability(good_customer) >= 0.9

    def test_bad_customer_low(self, bad_customer):
        assert calc_reliability(bad_customer) < 0.4

    def test_empty_returns_default(self):
        assert calc_reliability(None) == 0.5
        assert calc_reliability({}) == 0.5
        assert calc_reliability({'total_invoices': 0}) == 0.5

    def test_all_on_time(self):
        d = {'total_invoices': 100, 'paid_on_time': 100, 'not_paid': 0, 'avg_payment_delay': 0}
        assert calc_reliability(d) >= 0.95

    def test_all_unpaid(self):
        d = {'total_invoices': 10, 'paid_on_time': 0, 'not_paid': 10, 'avg_payment_delay': 0}
        assert calc_reliability(d) <= 0.15

    def test_high_delay_penalty(self):
        base = {'total_invoices': 10, 'paid_on_time': 8, 'paid_late': 2, 'not_paid': 0}
        low = {**base, 'avg_payment_delay': 5}
        high = {**base, 'avg_payment_delay': 35}
        assert calc_reliability(high) < calc_reliability(low)

    def test_always_bounded(self):
        for d in [
            {'total_invoices': 1, 'paid_on_time': 1, 'not_paid': 0, 'avg_payment_delay': 0},
            {'total_invoices': 1, 'paid_on_time': 0, 'not_paid': 1, 'avg_payment_delay': 100},
        ]:
            assert 0.1 <= calc_reliability(d) <= 1.0


class TestRiskAssessment:
    def test_low_risk(self, good_customer):
        score, level = calc_risk(good_customer, 1000)
        assert level == "Low" and score < 0.4

    def test_high_risk(self, bad_customer):
        score, level = calc_risk(bad_customer, 50000)
        assert level in ("High", "Medium")

    def test_large_invoice_increases_risk(self, good_customer):
        s1, _ = calc_risk(good_customer, 1000)
        s2, _ = calc_risk(good_customer, 100000)
        assert s2 >= s1

    def test_risk_factors_increase_risk(self):
        base = {'total_invoices': 10, 'paid_on_time': 5, 'paid_late': 3, 'not_paid': 2,
                'total_amount': 10000, 'avg_payment_delay': 10}
        s1, _ = calc_risk({**base, 'risk_factors': []})
        s2, _ = calc_risk({**base, 'risk_factors': ['A', 'B', 'C']})
        assert s2 >= s1

    def test_score_bounded(self, bad_customer):
        score, _ = calc_risk(bad_customer, 999999)
        assert 0.0 <= score <= 1.0


class TestCustomerMatching:
    def test_exact(self):
        assert find_match('Alice', {'Alice': {'name': 'Alice'}}) is not None

    def test_case_insensitive(self):
        assert find_match('alice', {'Alice': {'name': 'Alice'}}) is not None

    def test_partial(self):
        assert find_match('Alice', {'Alice Corp Intl': {'name': 'Alice Corp Intl'}}) is not None

    def test_special_chars(self):
        assert find_match('OBrien', {"O'Brien & Sons": {'name': "O'Brien"}}) is not None

    def test_no_match(self):
        assert find_match('Zzz', {'Alice': {'name': 'Alice'}}) is None

    def test_na_returns_none(self):
        h = {'Alice': {'name': 'Alice'}}
        assert find_match('N/A', h) is None
        assert find_match('', h) is None
        assert find_match(None, h) is None


class TestCSVLoading:
    def test_unique_customers(self, sample_csv):
        assert sample_csv['Customer_Name'].nunique() == 3

    def test_late_count(self, sample_csv):
        alice = sample_csv[sample_csv['Customer_Name'] == 'Alice Corp']
        assert alice['late_payment'].sum() == 1

    def test_amount_sum(self, sample_csv):
        bob = sample_csv[sample_csv['Customer_Name'] == 'Bob LLC']
        assert bob['amount'].sum() == 5500


class TestSafeFloat:
    def test_valid(self):
        assert safe_float("1,234.56") == 1234.56
        assert safe_float(42) == 42.0

    def test_invalid(self):
        assert safe_float(None) == 0.0
        assert safe_float("N/A") == 0.0
        assert safe_float("nan") == 0.0
        assert safe_float("") == 0.0
        assert safe_float("abc") == 0.0


class TestRiskLabels:
    def test_labels(self):
        def label(r):
            return "Low" if r >= 0.8 else "Medium" if r >= 0.6 else "High"
        assert label(0.9) == "Low"
        assert label(0.7) == "Medium"
        assert label(0.5) == "High"