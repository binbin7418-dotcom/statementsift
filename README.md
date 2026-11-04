# StatementSift

Convert a text-based bank statement PDF into a clean CSV — then reconcile
it: every running-balance step and the closing balance are verified, and
any break is reported with expected vs printed values.

## What it does

- Extracts transaction lines (date, description, amount, running balance)
  from digital statement PDFs; normalizes amounts and dates to ISO.
- Removes exact-duplicate transaction lines (logged, never silent).
- Reconciliation: opening + sum(amounts) vs the stated closing balance,
  plus a step-by-step running-balance chain check.
- Re-check: the cleaned CSV is re-parsed and re-verified independently;
  duplicates must be zero and any remaining chain break must be exactly
  the one already reported.
- Outputs `transactions.csv` (with per-row flags) and
  `reconciliation_report.md`.

Scope (stated honestly): text-based PDFs whose transaction lines end in
`<amount> <balance>`. Scanned/image statements need OCR and are out of
scope for this build.

100% local. No account, no upload, no subscription — the statement that
proves where your money went never leaves your computer.

## Batch mode (a folder of statements)

Real life is 12 months and several accounts, not one PDF. Point
StatementSift at a folder and it processes every statement PDF in it
(top level) in one pass:

- `transactions_merged.csv` — every period's cleaned transactions in one
  file, each row tagged with `source_file`, `period_start`, `period_end`,
  ordered chronologically by period.
- `cross_period_report.md` — one row per period (opening/closing balance,
  transaction count, whether the period opens exactly where the previous
  period closed, and the gap amount when it does not), plus a grand
  reconciliation across all periods that must tie out exactly.
  A PDF that cannot be parsed is listed as FAILED and skipped — it never
  aborts the batch and is never hidden.

```bash
python statement_sift.py --batch statements-folder/ out/
# (passing a folder as the first argument works too)
```

The GUI has the same batch mode: pick a folder and choose
"Convert folder (batch)".

## Run from source

```bash
python statement_sift.py statement.pdf out/
python statement_sift.py --batch statements-folder/ out/
python gui.py
```

## Acceptance

`test/acceptance_test.py` generates a seeded statement PDF (planted
duplicate line, planted +5.00 balance break, comma amounts, a DD/MM/YYYY
date) and asserts the exact conversion/reconciliation results.
See `test/ACCEPTANCE.md`.
