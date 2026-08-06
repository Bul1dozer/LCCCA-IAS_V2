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

