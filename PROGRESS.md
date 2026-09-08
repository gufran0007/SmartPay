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
