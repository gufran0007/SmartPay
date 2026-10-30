# Smart Pay progress

## 2026-09-08 - Portfolio design and invoice workflow improvements

- Reviewed the 20-page walkthrough.pdf. It is an older technical walkthrough, not a signed requirements document; preserve the newer tenant isolation and AI/manual import paths.
- Replaced the placeholder homepage with a responsive editorial layout and clearly labelled illustrative product preview.
- Rebuilt the overview around tenant-scoped invoice records, linked status totals, oldest overdue invoices, and first-use guidance. Removed misleading legacy-data dashboard charts.
- Combined invoice search and status filtering, added a no-results recovery action, accessible filter state, and dashboard filter links.
- Record payment dates when marking paid and clear them when reopening; overdue status is recalculated.
- Prevent reminders for paid invoices, use actual due dates for urgent wording, and allow startup without optional email credentials.
- Give invoice and import files unique names; sanitize legacy CSV upload paths.
- Isolate tests from local AI credentials and correct pytest discovery.

Validation: full suite 225 passed, 1 skipped (optional Gemini SDK check); desktop homepage/dashboard inspection and mobile invoice view at 390px; combined filters verified (zero matching overdue, one matching pending). No real email sent. Browser data uses a separate scratch database.

Scope and follow-up: this is a tested improvement pass, not a claim that every production path is audited. Review customer insights for database-only customers, edit-form validation, scanned PDF OCR availability, and CSRF coverage in subsequent runs. Real email delivery, external AI services and production deployment were not exercised. Existing .env.example deletion belongs to the user and is excluded from this commit.

Daily workflow: inspect status first, preserve unrelated work, choose one useful change, test and visually check it, commit only that change and push without force. Never publish credentials, database contents or customer uploads. Update this log with evidence and blockers.

## 2026-09-13 - Customer details without legacy CSV history

- Customers saved through invoice upload or canonical import now have a useful Details page listing their account's invoice records, amounts with original currencies, and links to each invoice.
- Replaced the unsupported default 50% reliability / High risk presentation with Not assessed for customers without legacy payment analysis.
- Support slashes in customer names and avoid decoding names twice. Missing or cross-account customers return HTTP 404.
- Validation: 32 focused customer and tenancy tests passed; browser verified Customers -> North Studio -> invoice summary using the isolated preview database. No real emails or external AI calls. Existing .env.example deletion left untouched.

## 2026-10-25 - Validate invoice edits on the server

- Invoice edit submissions now validate amounts, email addresses, dates, invoice numbers and VAT IDs before saving.
- Invalid submissions show field-specific errors and retain entered values; empty optional fields can be cleared.
- Render unavailable N/A values as blank inputs so browser validation does not reject existing records during a normal save.
- Validation: full suite 229 passed, 1 skipped (optional SDK check); browser verified invalid amount is rejected inline and retained. No email sent. Existing .env.example deletion preserved.

## 2026-10-26 - Keep reminder recipients tied to invoice records

- The reminder route now checks that its submitted recipient matches the invoice's saved, valid customer email; tampered addresses are rejected before SMTP opens.
- The message uses the saved address and SMTP connections close through a context manager.
- Validation: full suite 231 passed, 1 skipped (optional SDK); mocked SMTP tests confirmed recipient enforcement and connection lifecycle. Browser verified local registration and app access only; no real reminder was sent. Existing .env.example deletion preserved.

## 2026-10-30 - Validate legacy customer CSVs before storing

- Customer history uploads now reject empty/malformed files, files without a recognized customer-name header, and files with no usable customer names before they are written.
- Normalize header case and spacing so common title-cased CSV headers load consistently; successful imports are read once on later requests.
- Replaced raw parser/save exception details in redirect URLs with safe, actionable messages.
- Reset account upload folders within the isolated test uploads directory between tests, preventing reused account IDs from contaminating other cases.
- Validation: full suite 235 passed, 1 skipped (optional SDK check). Browser confirmed the CSV upload form and help format in a fresh local account; no file was uploaded. Existing .env.example deletion preserved.
