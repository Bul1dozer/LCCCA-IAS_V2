# Two-Host Educational Script

Host 1: Welcome to this LCCCA-IAS training session. Today we are walking through the school invoice system from the point of view of a staff member who needs to do real daily work: log in, check records, generate invoices, record payments, and keep financial records reliable.

Host 2: So we are not starting with technical details?

Host 1: Exactly. Think of LCCCA-IAS as the school's connected finance desk. It links learners, parents, fees, invoices, payments, balances, reports, and audit logs. Each part supports the next one.

Host 2: Where does a staff member begin?

Host 1: At the login page. The user enters a username and password. If the account is valid and active, the system opens a session and sends the user to the Dashboard. If the password is wrong, the system shows an error. If there are too many failed attempts, it tells the user to try again later.

Host 2: And if two-factor authentication is enabled?

Host 1: Then login has a second step. After the password, the system asks for a six-digit authenticator code. The user opens their authenticator app, enters the current code, and only then gets fully logged in.

Host 2: Once logged in, what does the Dashboard show?

Host 1: The Dashboard gives a quick overview: total learners, active learners, total parents, outstanding balance, payments received, invoices generated, invoices sent, collection rate, monthly collections, learners by grade, and recent activity.

Host 2: So it is like the daily health check.

Host 1: Yes. From there, staff usually confirm the people records. Start with Learners. A learner record includes full name, grade, class, date of admission, status, and physical address. When a learner is created, the system generates a unique learner number.

Host 2: Tell me about that learner number.

Host 1: It is intended to be permanent and unique. The normal format uses the admission date plus a sequence. For example, a learner admitted on 14 July 2026 might receive a number like 2026071401. Staff do not type this number in the normal learner form; the system creates it.

Host 2: What if I want to see only Grade 7 learners?

Host 1: The system supports grade grouping and grade filtering in the backend, and the Dashboard can show learners by grade. On the current Learners screen, staff can use search to narrow the table by typing a grade such as Grade 7. A dedicated grade dropdown was not found on that screen.

Host 2: After learners, we need parents?

Host 1: Correct. On Parents & Guardians, staff create the parent or guardian record. The form includes full name, email, phone, physical address, employer name, position, employer address, and employer phone. The backend also stores other contact fields, such as WhatsApp number and preferred channel, but those are not all shown on the current form.

Host 2: Let us use an example.

Host 1: Imagine one parent, Mrs. Nambili, with three children at the school. Staff open the parent profile, choose Link Learner, select each child, and choose the relationship type, such as Mother or Guardian. Now the parent profile shows all three linked learners.

Host 2: Why is that link so important?

Host 1: Because parent invoices depend on it. If a child is not linked to the correct parent, that child will not appear on the parent's family invoice. If the wrong child is linked, billing can be wrong. Staff should always verify linked learners before invoicing.

Host 2: Now fees. How are fees set up?

Host 1: Fees live in the Fee Catalogue. A fee item has a name, category, frequency, amount, mandatory setting, active setting, and applicable grades or classes. Examples are monthly tuition, registration fees, sports and culture, after-school care, and transport.

Host 2: What is the difference between mandatory and optional fees?

Host 1: Mandatory fees can be auto-assigned to learners when they match the learner's grade or class. Optional fees, such as after-school care or transport, can be assigned manually when they apply.

Host 2: And learner-specific fee profiles?

Host 1: In the Learner Profiles tab, staff select a learner and see assigned fees, the monthly total, active items, and grade. Staff can add fees, remove assignments, or use Auto-Assign to apply matching mandatory fees.

Host 2: Now the big one: invoice generation.

Host 1: The Invoices screen has a Generate Invoice button. The form asks whether the invoice is for a Learner or for a Parent, specifically Parent aggregate linked learners.

Host 2: When should staff use learner mode?

Host 1: Use learner mode when billing one learner individually. Select the learner, choose the billing period and due date, and generate the invoice. The system creates invoice items and posts a debit to that learner's ledger.

Host 2: And parent mode?

Host 1: Use parent mode when one parent should receive one combined invoice for linked children. Select Parent aggregate linked learners, choose the parent, billing period, and due date. The system gathers active linked learners, checks each child's monthly fee assignments, skips learners already invoiced for that billing period, and creates one parent invoice.

Host 2: Does that mean all money goes to the parent account?

Host 1: The invoice is billed to the parent, but the financial ledger remains learner-specific. That is a key idea. One parent invoice can show all children, but each learner's charges are still posted to that learner's balance.

Host 2: Give me the three-child example.

Host 1: Mrs. Nambili has three linked learners. Learner A has N$ 1,838 monthly tuition, Learner B has N$ 1,838 tuition plus N$ 50 sports and culture, and Learner C has N$ 2,650 tuition plus N$ 100 sports and culture. The system creates one invoice number, one combined invoice total, separate line items showing which learner each fee belongs to, and separate debit ledger entries for each learner.

Host 2: What protects the school from duplicate invoices?

Host 1: The system checks whether each learner has already been invoiced for that billing period. If one child has already been invoiced, that child is skipped and eligible siblings can still be invoiced. If all children have already been invoiced, the system refuses to create another invoice for that period.

Host 2: How does staff review invoices?

Host 1: The Invoices table shows Invoice Number, Parent, Linked Learners, Billing Period, Issue Date, Due Date, Current Charges, Previous Balance, Payments, Outstanding Balance, Status, and Actions. Staff can expand a row to view learner-specific line items. They can preview or download the PDF, send the invoice by email, or void it if needed.

Host 2: What about payments?

Host 1: On Payments, staff choose Capture Payment. They select the learner, enter amount paid, date paid, payment method, optional reference number, and notes. The learner dropdown shows names and learner numbers, which helps avoid selecting by balance alone.

Host 2: Why learner, not parent?

Host 1: Because balances are learner-specific. Even if the parent receives one invoice, the payment credit must reduce the correct learner's ledger balance. Staff should be careful here.

Host 2: What if the parent pays only part of the balance?

Host 1: Partial payments are supported. The payment is posted as a credit. The outstanding balance is calculated from ledger debits minus credits.

Host 2: And if the same reference number is entered twice?

Host 1: The system rejects duplicate active payment references. That helps prevent accidental duplicate capture.

Host 2: Can staff view receipts?

Host 1: Yes. Payment rows have a receipt action that opens a PDF receipt.

Host 2: What reports are available?

Host 1: Reports include Outstanding Balances, Monthly Collections, Learner Statement, and Payment History. The Reports screen also shows a report generation log.

Host 2: What does the audit log do?

Host 1: The audit log records important actions, such as creating learners, linking records, generating invoices, recording payments, reversing payments, changing passwords, enabling 2FA, and running month-end. It shows who did the action and when.

Host 2: Let us talk password reset.

Host 1: On the login page, the user can request password recovery by entering username and email. The system gives a generic response so it does not reveal whether the account exists. If the details match, it creates a time-limited, single-use reset token. In normal mode, the raw token is not shown in the browser.

Host 2: And changing a password while logged in?

Host 1: In Settings, the user enters current password, new password, and confirmation. The system checks the current password, checks that both new fields match, requires at least eight characters, and prevents reusing the same password.

Host 2: How does staff enable 2FA?

Host 1: In Settings, open the 2FA tab, choose setup, scan the QR code in an authenticator app, then enter a current code. Setup is only complete after the code is accepted. To disable 2FA, the user must provide the current password and a valid code.

Host 2: Are there any caution points?

Host 1: Yes. Demo credentials are present in the login page and seed data, so they must be changed before real use. Data Import has a current implementation mismatch around import job logging. Payment editing appears in the UI, but the backend update route was not found. Automatic scheduled month-end should be verified in deployment, because the scheduler code exists but the main app does not visibly start it.

Host 2: So what is the safest staff checklist?

Host 1: Before billing, confirm the parent, confirm linked learners, confirm grade, confirm fee profile, confirm billing period and due date, generate once, review line items, then send or download the invoice. For payments, confirm the learner number before saving. And at the end of the day, review reports if needed and log out.

Host 2: That makes the system less intimidating.

Host 1: Exactly. LCCCA-IAS is there to keep school billing connected, traceable, and easier to review. The more carefully staff maintain links, fees, and payment references, the more reliable the financial records become.

