"""
Invoice Service for Smart Pay
Handles invoice extraction, parsing, and storage
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from datetime import datetime

from dateutil import parser as dateparser
from PIL import Image, ImageEnhance, ImageFilter

# Optional imports with fallbacks
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

from app.models.database import SessionLocal, Invoice, InvoiceFeatures
from app.services.paths import UPLOAD_DIR


class InvoiceService:
    """Service for extracting and processing invoice data"""
    
    def __init__(self):
        self.supported_extensions = {'.pdf', '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif'}
    
    def extract_text(self, file_path: Path) -> str:
        """Extract text from PDF or image file"""
        ext = file_path.suffix.lower()
        
        if ext == '.pdf':
            return self._extract_from_pdf(file_path)
        elif ext in {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif'}:
            return self._extract_from_image(file_path)
        
        return ""
    
    def _extract_from_pdf(self, file_path: Path) -> str:
        """Extract text from PDF using PyMuPDF"""
        if fitz is None:
            print("⚠️ PyMuPDF not available")
            return ""
        
        try:
            text_parts = []
            with fitz.open(file_path) as pdf:
                for page in pdf:
                    # Try structured extraction first
                    blocks = page.get_text("dict").get("blocks", [])
                    structured = []
                    
                    for block in blocks:
                        if "lines" not in block:
                            continue
                        block_text = []
                        for line in block["lines"]:
                            line_text = " ".join(span["text"] for span in line["spans"]).strip()
                            if line_text:
                                block_text.append(line_text)
                        if block_text:
                            x0, y0 = block["bbox"][:2]
                            structured.append((y0, x0, "\n".join(block_text)))
                    
                    # Sort by position (top to bottom, left to right)
                    structured.sort(key=lambda t: (t[0], t[1]))
                    layout_text = "\n".join(t[2] for t in structured)
                    
                    # Also get plain text as fallback
                    plain_text = page.get_text() or ""
                    
                    # Use the longer extraction
                    text_parts.append(layout_text if len(layout_text) >= len(plain_text) else plain_text)
            
            text = "\n\n".join(text_parts)
            return self._clean_text(text)
            
        except Exception as e:
            print(f"❌ PDF extraction error: {e}")
            return ""
    
    def _extract_from_image(self, file_path: Path) -> str:
        """Extract text from image using Tesseract OCR"""
        if pytesseract is None:
            print("⚠️ Tesseract not available")
            return ""
        
        try:
            img = Image.open(file_path)
            img = self._preprocess_image(img)
            text = pytesseract.image_to_string(img, config=r'--oem 3 --psm 6')
            return self._clean_text(text)
            
        except Exception as e:
            print(f"❌ Image OCR error: {e}")
            return ""
    
    def _preprocess_image(self, img: Image.Image) -> Image.Image:
        """Preprocess image for better OCR results"""
        # Convert to grayscale
        if img.mode != 'L':
            img = img.convert('L')
        
        # Enhance contrast
        img = ImageEnhance.Contrast(img).enhance(2.0)
        img = ImageEnhance.Sharpness(img).enhance(2.0)
        
        # Scale up if too small
        w, h = img.size
        if w < 1200:
            scale = 1200 / w
            img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
        
        # Sharpen
        img = img.filter(ImageFilter.SHARPEN)
        
        return img
    
    def _clean_text(self, text: str) -> str:
        """Clean extracted text"""
        if not text:
            return ""
        
        # Normalize line endings
        text = re.sub(r'\r\n|\r', '\n', text)
        # Normalize spaces
        text = re.sub(r'[ \t]+', ' ', text)
        # Remove excessive blank lines
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Fix split numbers
        text = re.sub(r'(\d)\s+(\d)', r'\1\2', text)
        text = re.sub(r'(\d)\s*,\s*(\d)', r'\1,\2', text)
        
        return text.strip()
    
    def parse_invoice(self, text: str) -> Dict[str, str]:
        """Parse invoice text and extract structured data"""
        data = {
            'invoice_number': self._extract_invoice_number(text),
            'date': 'N/A',
            'due_date': 'N/A',
            'amount': 'N/A',
            'currency': 'N/A',
            'customer_name': 'N/A',
            'customer_email': 'N/A',
            'customer_address': 'N/A',
            'company_name': 'N/A',
            'vat_id': 'N/A'
        }
        
        # Extract dates
        data['date'], data['due_date'] = self._extract_dates(text)
        
        # Extract amount and currency
        data['amount'], data['currency'] = self._extract_amount(text)
        
        # Extract customer info
        customer = self._extract_customer_info(text)
        data['customer_name'] = customer.get('name', 'N/A')
        data['customer_email'] = customer.get('email', 'N/A')
        data['customer_address'] = customer.get('address', 'N/A')
        
        # Extract company info
        company = self._extract_company_info(text)
        data['company_name'] = company.get('name', 'N/A')
        data['vat_id'] = company.get('vat', 'N/A')
        
        return self._validate_data(data)
    
    def _extract_invoice_number(self, text: str) -> str:
        """Extract invoice number from text"""
        patterns = [
            r'Invoice\s*(?:No\.?|Number|#|ID|Ref)[:\-]?\s*([A-Za-z0-9\-/]+)',
            r'INVOICE\s*#\s*([A-Za-z0-9\-/]+)',
            r'Bill\s*(?:No\.?|Number|#)[:\-]?\s*([A-Za-z0-9\-/]+)',
            r'\b(?:INV|BILL|ORD)[\s\-]?[A-Z]?\d{4}[\s\-]?\d+\b',
            r'\b[A-Z]{2,}-\d{4}-\d+\b',
            r'\b\d{4,}-\d{3,}\b',
            r'\b[A-Z]+\d{6,}\b',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple):
                    match = match[0]
                if match and len(match) > 3:
                    return match.strip()
        
        return 'N/A'
    
    def _extract_dates(self, text: str) -> Tuple[str, str]:
        """Extract invoice date and due date"""
        invoice_date, due_date = 'N/A', 'N/A'
        
        # Date patterns
        date_patterns = [
            r'\d{1,2}\s+[A-Za-z]+\s+\d{4}',
            r'\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}',
            r'[A-Za-z]+\s+\d{1,2},?\s+\d{4}',
        ]
        
        # Invoice date patterns
        invoice_labels = [
            r'Invoice\s+Date[:\-\s]?\s*([^\n]+)',
            r'Date\s+(?:of\s+Invoice)?[:\-]?\s*([^\n]+)',
            r'Issued\s+(?:on|Date)?[:\-\s]?\s*([^\n]+)',
            r'Billing\s+Date[:\-\s]?\s*([^\n]+)',
        ]
        
        # Due date patterns
        due_labels = [
            r'Due\s+Date[:\-\s]?\s*([^\n]+)',
            r'Payment\s+Due[:\-\s]?\s*([^\n]+)',
            r'Pay\s+By[:\-\s]?\s*([^\n]+)',
            r'Due[:\-\s]+([^\n]+)',
        ]
        
        def find_date(label_patterns: List[str]) -> str:
            for label in label_patterns:
                match = re.search(label, text, re.IGNORECASE)
                if match:
                    chunk = match.group(1).strip()
                    for dp in date_patterns:
                        dm = re.search(dp, chunk)
                        if dm:
                            try:
                                dt = dateparser.parse(dm.group(), dayfirst=True, fuzzy=True)
                                if dt:
                                    return dt.strftime('%Y-%m-%d')
                            except Exception:
                                pass
            return 'N/A'
        
        invoice_date = find_date(invoice_labels)
        due_date = find_date(due_labels)
        
        return invoice_date, due_date
    
    def _extract_amount(self, text: str) -> Tuple[str, str]:
        """Extract amount and currency"""
        amount, currency = 'N/A', 'N/A'
        
        # Amount patterns (order by specificity)
        amount_patterns = [
            r'Total[:\-\s]*([€$£]?\s?[\d,.]+)',
            r'Amount\s+Due[:\-\s]*([€$£]?\s?[\d,.]+)',
            r'Grand\s+Total[:\-\s]*([€$£]?\s?[\d,.]+)',
            r'Invoice\s+(?:Amount|Total)[:\-\s]*([€$£]?\s?[\d,.]+)',
            r'Balance\s+Due[:\-\s]*([€$£]?\s?[\d,.]+)',
            r'([\d,]+\.[\d]{2})\s*(EUR|USD|GBP|JPY|CAD|AUD|CHF|CNY|INR|PKR)',
            r'(EUR|USD|GBP|JPY|CAD|AUD|CHF|CNY|INR|PKR)\s*([\d,]+\.[\d]{2})',
            r'[€$£¥₹]\s*([\d,]+\.[\d]{2})',
            r'([\d,]+\.[\d]{2})\s*[€$£¥₹]',
        ]
        
        found_amounts = []
        for pattern in amount_patterns:
            for match in re.findall(pattern, text, re.IGNORECASE):
                if isinstance(match, tuple):
                    for part in match:
                        if re.search(r'\d', part):
                            found_amounts.append(part)
                else:
                    found_amounts.append(match)
        
        # Sort by value (descending) and take the largest
        def to_float(val: str) -> float:
            val = re.sub(r'[^\d,.]', '', val)
            if ',' in val and '.' in val:
                if val.rfind(',') > val.rfind('.'):
                    val = val.replace('.', '').replace(',', '.')
                else:
                    val = val.replace(',', '')
            elif ',' in val:
                parts = val.split(',')
                if len(parts) == 2 and len(parts[1]) in (2, 3):
                    val = val.replace(',', '.')
                else:
                    val = val.replace(',', '')
            try:
                return float(val)
            except ValueError:
                return 0.0
        
        if found_amounts:
            found_amounts.sort(key=to_float, reverse=True)
            amount = self._normalize_amount(found_amounts[0])
        
        # Detect currency
        upper_text = text.upper()
        for code in ['EUR', 'USD', 'GBP', 'JPY', 'CAD', 'AUD', 'CHF', 'CNY', 'INR', 'PKR']:
            if code in upper_text:
                currency = code
                break
        
        if currency == 'N/A':
            if '€' in text:
                currency = 'EUR'
            elif '$' in text:
                currency = 'USD'
            elif '£' in text:
                currency = 'GBP'
            elif '¥' in text:
                currency = 'JPY'
            elif '₹' in text:
                currency = 'INR'
        
        return amount, currency
    
    def _normalize_amount(self, val: str) -> str:
        """Normalize amount string to standard format"""
        if not val or val == 'N/A':
            return 'N/A'
        
        val = re.sub(r'[^\d,.]', '', val.strip())
        
        if ',' in val and '.' in val:
            if val.rfind(',') > val.rfind('.'):
                val = val.replace('.', '').replace(',', '.')
            else:
                val = val.replace(',', '')
        elif ',' in val:
            parts = val.split(',')
            if len(parts) == 2 and len(parts[1]) in (2, 3):
                val = val.replace(',', '.')
            else:
                val = val.replace(',', '')
        
        try:
            return str(round(float(val), 2))
        except ValueError:
            return 'N/A'
    
    def _extract_customer_info(self, text: str) -> Dict[str, str]:
        """Extract customer information"""
        info = {'name': 'N/A', 'email': 'N/A', 'address': 'N/A'}
        
        # Find customer section
        section_patterns = [
            r'BILLED?\s+TO[:\s]*(.+?)(?=\s*(?:INVOICE|FROM|DATE|AMOUNT|DESCRIPTION|$))',
            r'CUSTOMER[:\s]*(.+?)(?=\s*(?:INVOICE|FROM|DATE|AMOUNT|$))',
            r'CLIENT[:\s]*(.+?)(?=\s*(?:INVOICE|FROM|DATE|AMOUNT|$))',
            r'SHIP\s+TO[:\s]*(.+?)(?=\s*(?:INVOICE|FROM|DATE|AMOUNT|$))',
        ]
        
        section = ''
        for pattern in section_patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                section = match.group(1).strip()
                break
        
        if section:
            lines = [l.strip() for l in section.split('\n') if l.strip()]
            
            # Extract email
            for line in lines:
                email_match = re.search(r'([\w.%+-]+@[\w.-]+\.[A-Za-z]{2,})', line)
                if email_match:
                    info['email'] = email_match.group(1)
                    break
            
            # Extract name (first non-email, non-numeric line)
            for line in lines:
                if '@' not in line and not re.search(r'\d{5}', line):
                    if len(line.split()) > 1 or not info['email']:
                        info['name'] = line
                        break
            
            # Extract address
            address_parts = []
            for line in lines:
                if (re.search(r'\d+\s*[\w\s]+', line) or
                    re.search(r'(Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr)', line, re.I) or
                    re.search(r'\d{4,5}\s+[A-Za-z]', line)):
                    address_parts.append(line)
            
            if address_parts:
                info['address'] = ', '.join(address_parts)
        
        # Fallback: find email anywhere
        if info['email'] == 'N/A':
            email_match = re.search(r'([\w.%+-]+@[\w.-]+\.[A-Za-z]{2,})', text)
            if email_match:
                info['email'] = email_match.group(1)
        
        # Derive name from email if needed
        if info['name'] == 'N/A' and info['email'] != 'N/A':
            name_part = info['email'].split('@')[0]
            name_part = re.sub(r'[0-9._+-]', ' ', name_part).title().strip()
            info['name'] = name_part or 'N/A'
        
        return info
    
    def _extract_company_info(self, text: str) -> Dict[str, str]:
        """Extract company/seller information"""
        info = {'name': 'N/A', 'address': 'N/A', 'vat': 'N/A'}
        
        # Company patterns
        company_patterns = [
            r'FROM[:\s]*(.+?)(?=\s*(?:BILLED|TO|CUSTOMER|INVOICE|$))',
            r'SELLER[:\s]*(.+?)(?=\s*(?:BILLED|TO|CUSTOMER|INVOICE|$))',
            r'Company\s*Name?[:\-\s]+([^\n]+)',
            r'Vendor[:\-\s]+([^\n]+)',
        ]
        
        for pattern in company_patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                section = match.group(1).strip()
                lines = [l.strip() for l in section.split('\n') if l.strip()]
                if lines:
                    info['name'] = lines[0]
                break
        
        # Extract VAT/Tax ID
        vat_patterns = [
            r'VAT\s*(?:ID|Number|No\.?)[:\-\s]?\s*([A-Z0-9]+)',
            r'Tax\s*(?:ID|Number|No\.?)[:\-\s]?\s*([A-Z0-9]+)',
            r'GSTIN[:\-\s]?\s*([A-Z0-9]+)',
            r'\b([A-Z]{2}\d{9,12})\b',
        ]
        
        for pattern in vat_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                info['vat'] = match.group(1).strip()
                break
        
        return info
    
    def _validate_data(self, data: Dict[str, str]) -> Dict[str, str]:
        """Validate and clean extracted data"""
        # Validate amount
        if data.get('amount') not in (None, 'N/A'):
            try:
                float(data['amount'])
            except ValueError:
                data['amount'] = 'N/A'
        
        # Validate invoice number
        inv_num = data.get('invoice_number', 'N/A')
        if inv_num != 'N/A' and not re.search(r'[A-Za-z0-9]', inv_num):
            data['invoice_number'] = 'N/A'
        
        # Validate email
        email = data.get('customer_email', 'N/A')
        if email != 'N/A' and not re.match(r'^[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}$', email.strip()):
            data['customer_email'] = 'N/A'
        
        # Truncate long fields
        for field in ['customer_name', 'company_name', 'invoice_number', 'customer_address']:
            if field in data and data[field] and len(data[field]) > 255:
                data[field] = data[field][:255]
        
        return data
    
    def extract_and_save(self, filename: str) -> Optional[int]:
        """Extract invoice data from file and save to database"""
        file_path = UPLOAD_DIR / filename
        
        if not file_path.exists():
            print(f"❌ File not found: {file_path}")
            return None
        
        # Extract text
        text = self.extract_text(file_path)
        if not text:
            print(f"⚠️ No text extracted from: {filename}")
            return None
        
        # Parse data
        data = self.parse_invoice(text)
        data['filename'] = filename
        
        # Save to database
        db = SessionLocal()
        try:
            invoice = Invoice(
                customer_name=data.get('customer_name', 'N/A'),
                customer_email=data.get('customer_email', 'N/A'),
                customer_address=data.get('customer_address', 'N/A'),
                invoice_number=data.get('invoice_number', 'N/A'),
                amount=data.get('amount', 'N/A'),
                currency=data.get('currency', 'N/A'),
                date=data.get('date', 'N/A'),
                due_date=data.get('due_date', 'N/A'),
                company_name=data.get('company_name', 'N/A'),
                vat_id=data.get('vat_id', 'N/A'),
                filename=filename
            )
            
            db.add(invoice)
            db.commit()
            db.refresh(invoice)
            
            # Create empty features record
            features = InvoiceFeatures(
                invoice_id=invoice.id,
                amount=invoice.amount_float
            )
            db.add(features)
            db.commit()
            
            print(f"✅ Saved invoice #{invoice.id} from {filename}")
            return invoice.id
            
        except Exception as e:
            db.rollback()
            print(f"❌ Database error: {e}")
            return None
        finally:
            db.close()
