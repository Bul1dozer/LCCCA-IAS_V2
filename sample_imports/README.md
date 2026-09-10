# LCCCA-IAS Sample Import Data

These files contain disposable mock data for testing imports. They are not production records.

Recommended order:

1. Import `parents.csv` from Data Import -> Import Parents.
2. Import `learners.csv` from Data Import -> Import Learners.
3. Link parents to learners manually using `parent_learner_links_manual.csv` as a reference.
4. Import `payments.csv` from Data Import -> Import Payments.
5. Import `bank_statement.csv` from Bank Reconciliation -> Import Statement.

The payment reference numbers in `payments.csv` match the bank references in `bank_statement.csv`, so reconciliation can auto-match where amounts also match.

Current limitation: there is no parent-learner relationship import endpoint yet. The `parent_learner_links_manual.csv` file is a checklist for manual linking on the Parents screen.

## Larger Test Packs

Two fresh-start learner import packs are included:

- `parents_10.csv`, `learners_10.csv`, `parent_learner_links_manual_10.csv`, `payments_10.csv`, `bank_statement_10.csv`
- `parents_100.csv`, `learners_100.csv`, `parent_learner_links_manual_100.csv`, `payments_100.csv`, `bank_statement_100.csv`

Use the same order as above. The 10-learner pack is best for quick UI checks. The 100-learner pack is best for import, search, invoice generation, and reconciliation volume testing.

The payment files include payments for the first 10 learners in the 10-pack and the first 30 learners in the 100-pack. Each bank statement file includes matching references plus one intentionally unmatched line.

## Comprehensive Fresh 10-Learner Pack

Use this pack after clearing the database when you want one parent per learner and payment/bank-recon data for every learner:

1. Import `fresh_parents_10.csv` from Data Import -> Import Parents.
2. Import `fresh_learners_10.csv` from Data Import -> Import Learners.
3. Link parents to learners manually using `fresh_parent_learner_links_manual_10.csv`.
4. Import `fresh_payments_10.csv` from Data Import -> Import Payments.
5. Import `fresh_bank_statement_10.csv` from Bank Reconciliation -> Import Statement.

This pack includes 10 parents, 10 learners, 10 matching payments, 10 matching bank statement lines, and 1 intentionally unmatched bank line. All email addresses use `example.test` and are safe mock addresses.
