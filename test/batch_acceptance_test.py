#!/usr/bin/env python3
"""Batch acceptance test for StatementSift folder mode.

Builds a 3-period batch in test/batch_in/:
  - 2026-01: the shipped sample_statement.pdf (opening 10,000.00, stated
    closing 12,580.25, computed 12,575.25 — planted +5.00 internal break,
    5 kept transactions after dedupe)
  - 2026-02: synthesized; opens at 12,580.25 (exactly January's stated
    closing -> link OK), clean chain, closing 14,129.50, 3 transactions
  - 2026-03: synthesized; opens at 14,200.00 although February closed at
    14,129.50 -> deliberate cross-period GAP of +70.50; internally clean,
    closing 16,500.00, 2 transactions

Expected: 3 files processed, 0 failed, 10 merged transactions,
exactly 1 link gap (+70.50), grand reconciliation PASS
(residual +75.50 == internal close diffs +5.00 + link gaps +70.50),
re-check PASS.
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from statement_sift import run_batch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IN = os.path.join(HERE, "batch_in")
OUT = os.path.join(HERE, "batch_out")

FEB = [
    "Statement period: 2026-02-01 to 2026-02-28",
    "Opening balance: 12,580.25",
    "2026-02-02 Salary - ACME Ltd 2,500.00 15,080.25",
    "2026-02-03 Rent payment -800.00 14,280.25",
    "2026-02-10 Groceries -150.75 14,129.50",
    "Closing balance: 14,129.50",
]

MAR = [
    "Statement period: 2026-03-01 to 2026-03-31",
    "Opening balance: 14,200.00",
    "2026-03-02 Salary - ACME Ltd 2,500.00 16,700.00",
    "2026-03-05 Utility bill -200.00 16,500.00",
    "Closing balance: 16,500.00",
]


def make_pdf(path, lines):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(path, pagesize=A4)
    c.setFont("Courier", 10)
    y = 800
    for line in lines:
        c.drawString(40, y, line)
        y -= 18
    c.save()


def main():
    os.makedirs(IN, exist_ok=True)
    shutil.copyfile(os.path.join(ROOT, "sample_statement.pdf"),
                    os.path.join(IN, "2026-01_statement.pdf"))
    make_pdf(os.path.join(IN, "2026-02_statement.pdf"), FEB)
    make_pdf(os.path.join(IN, "2026-03_statement.pdf"), MAR)

    s = run_batch(IN, OUT)
    checks = []

    def chk(name, cond):
        checks.append((name, bool(cond)))

    chk("files found == 3", s["files_found"] == 3)
    chk("processed == 3", s["processed"] == 3)
    chk("failed == 0", s["failed"] == 0)
    chk("total transactions == 10 (5 Jan + 3 Feb + 2 Mar)", s["total_txns"] == 10)
    chk("exactly 1 cross-period link gap", s["link_gaps"] == 1)
    chk("grand reconciliation PASS", s["grand_ok"] is True)
    chk("recheck_ok is True", s["recheck_ok"] is True)
    chk("total amount == 6424.50", s["total_amount"] == "6424.50")

    import csv as _csv
    rows = list(_csv.DictReader(open(s["merged_csv_path"], encoding="utf-8")))
    chk("merged CSV rows == 10", len(rows) == 10)
    chk("every merged row tagged with source_file + period",
        all(r["source_file"] and r["period_start"] and r["period_end"] for r in rows))
    chk("merged CSV covers all 3 source files",
        {r["source_file"] for r in rows} == {"2026-01_statement.pdf",
                                             "2026-02_statement.pdf",
                                             "2026-03_statement.pdf"})
    chk("merged CSV chronological by period (Jan rows first, Mar rows last)",
        rows[0]["source_file"] == "2026-01_statement.pdf"
        and rows[-1]["source_file"] == "2026-03_statement.pdf")
    chk("January BALANCE_BREAK flag carried into merged CSV",
        sum(1 for r in rows if r["flags"] == "BALANCE_BREAK") == 1)

    rep = open(s["report_path"], encoding="utf-8").read()
    chk("report flags the deliberate GAP +70.50", "GAP" in rep and "+70.50" in rep)
    chk("report names both files around the gap",
        "2026-02_statement.pdf" in rep and "2026-03_statement.pdf" in rep)
    chk("report grand check PASS", "Grand check: PASS" in rep)
    chk("report residual +75.50 == +5.00 internal + +70.50 gap",
        "+75.50" in rep and "+5.00" in rep)

    failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(("PASS " if ok else "FAIL ") + n)
    print(f"\nsummary: {s}")
    print("BATCH ACCEPTANCE:", "PASS" if not failed else f"FAIL ({len(failed)})")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
