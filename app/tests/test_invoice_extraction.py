import re


def extract_all(text):
    data = {'invoice_number': 'N/A', 'email': 'N/A', 'amount': 'N/A',
            'currency': 'N/A', 'date': 'N/A', 'due_date': 'N/A'}
    if not text: return data

    m = re.search(r'(?:invoice|inv)[:\s#]*([A-Z0-9\-]+)', text, re.I)
    if m: data['invoice_number'] = m.group(1).strip()

    m = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
    if m: data['email'] = m.group(0)

    m = re.search(r'(?:total|amount)[:\s]*[$€£]?\s*([\d,]+\.?\d*)', text, re.I)
    if m: data['amount'] = m.group(1).replace(',', '')

    if '$' in text: data['currency'] = 'USD'
    elif '€' in text: data['currency'] = 'EUR'
    elif '£' in text: data['currency'] = 'GBP'

    dates = re.findall(r'(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})', text)
    if dates:
        data['date'] = dates[0]
        if len(dates) > 1: data['due_date'] = dates[1]
    return data


class TestInvoiceNumber:
    def test_standard(self):
        assert extract_all("Invoice #INV-2024-001")['invoice_number'] == "INV-2024-001"

    def test_with_colon(self):
        assert extract_all("Invoice: 12345")['invoice_number'] == "12345"

    def test_not_found(self):
        assert extract_all("No invoice info here")['invoice_number'] == "N/A"


class TestEmailExtraction:
    def test_found(self):
        assert extract_all("Email: john@example.com")['email'] == "john@example.com"

    def test_dotted(self):
        assert extract_all("first.last@company.co.uk")['email'] == "first.last@company.co.uk"

    def test_not_found(self):
        assert extract_all("No email")['email'] == "N/A"

    def test_in_invoice(self, invoice_text):
        assert extract_all(invoice_text)['email'] == "john@example.com"


class TestAmountExtraction:
    def test_simple(self):
        assert extract_all("Total: 500.00")['amount'] == "500.00"

    def test_with_commas(self):
        assert extract_all("Total: 1,234.56")['amount'] == "1234.56"

    def test_with_euro(self):
        assert extract_all("Total: €5,000.00")['amount'] == "5000.00"

    def test_with_dollar(self):
        assert extract_all("Amount: $12,500.00")['amount'] == "12500.00"

    def test_not_found(self):
        assert extract_all("No amounts")['amount'] == "N/A"


class TestCurrency:
    def test_usd(self):
        assert extract_all("Total: $500")['currency'] == "USD"

    def test_eur(self):
        assert extract_all("Total: €500")['currency'] == "EUR"

    def test_gbp(self):
        assert extract_all("Total: £500")['currency'] == "GBP"

    def test_none(self):
        assert extract_all("Total: 500")['currency'] == "N/A"

    def test_in_invoice(self, invoice_text):
        assert extract_all(invoice_text)['currency'] == "EUR"


class TestDateExtraction:
    def test_two_dates(self, invoice_text):
        d = extract_all(invoice_text)
        assert d['date'] != "N/A" and d['due_date'] != "N/A"

    def test_slash(self):
        assert extract_all("Date: 15/01/2024")['date'] == "15/01/2024"

    def test_dash(self):
        assert extract_all("Date: 15-01-2024")['date'] == "15-01-2024"

    def test_dot(self):
        assert extract_all("Date: 15.01.2024")['date'] == "15.01.2024"

    def test_no_dates(self):
        d = extract_all("No dates here")
        assert d['date'] == "N/A" and d['due_date'] == "N/A"

    def test_single_date(self):
        d = extract_all("Date: 01/15/2024")
        assert d['date'] == "01/15/2024" and d['due_date'] == "N/A"


class TestFullPipeline:
    def test_eur_invoice(self, invoice_text):
        d = extract_all(invoice_text)
        assert d['invoice_number'] != "N/A"
        assert d['email'] == "john@example.com"
        assert d['currency'] == "EUR"

    def test_empty(self):
        d = extract_all("")
        assert all(v == "N/A" for v in d.values())

    def test_none(self):
        d = extract_all(None)
        assert all(v == "N/A" for v in d.values())