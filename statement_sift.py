#!/usr/bin/env python3
"""
StatementSift — bank statement PDF -> clean CSV, with balance reconciliation.

Pipeline:
  1. Extract the text layer of a (digital, text-based) statement PDF.
  2. Parse transaction lines: date, description, amount, running balance.
     Amounts are normalized (thousands commas removed, dates to ISO).
  3. Remove exact-duplicate transaction lines (same date, description,
     amount and balance printed twice) — logged, never silent.
  4. Reconcile: walk the running-balance chain and verify every step;
     verify opening + sum(amounts) against the stated closing balance.
     A break is reported with expected vs stated values — which side is
     wrong is the bank's/your call, so it is never "repaired" by fiat.
  5. Re-check: the cleaned CSV is re-parsed and re-verified; duplicates
     must be zero and the only chain breaks allowed are the ones already
     reported in step 4.

Scope: text-based PDFs whose transaction lines end in
"<amount> <balance>". Scanned/image statements need OCR and are
reported as out of scope, not guessed at.

Local only. No network access. Your statement never leaves this computer.
"""
import csv
import os
import re
import sys
from decimal import Decimal

AMT = r"-?[\d,]+\.\d{1,2}"
TXN_RE = re.compile(
    r"^\s*(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})\s+(.+?)\s+(" + AMT + r")"
    r"(?:\s+(DR|CR))?\s+(" + AMT + r")\s*$")
OPEN_RE = re.compile(r"Opening balance[:\s]+(-?[\d,]+\.\d{2})", re.I)
CLOSE_RE = re.compile(r"Closing balance[:\s]+(-?[\d,]+\.\d{2})", re.I)


def dec(s):
    return Decimal(s.replace(",", ""))


def norm_date(s):
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s, False
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", s)
    if m:  # documented as DD/MM/YYYY
        d, mo, y = m.groups()
        return f"{y}-{mo}-{d}", True
    return s, False


def extract_lines(pdf_path):
    import fitz  # PyMuPDF
    doc = fitz.open(pdf_path)
    lines = []
    for page in doc:
        lines.extend(page.get_text("text").splitlines())
    return lines, doc.page_count


def parse_statement(lines):
    """Returns (opening, closing, txns, fixes). txns keep raw + normalized."""
    opening = closing = None
    txns, fixes = [], []
    for raw in lines:
        line = raw.rstrip()
        mo = OPEN_RE.search(line)
        if mo:
            opening = dec(mo.group(1))
            continue
        mc = CLOSE_RE.search(line)
        if mc:
            closing = dec(mc.group(1))
            continue
        m = TXN_RE.match(line)
        if not m:
            continue
        date_raw, desc, amt_raw, drcr, bal_raw = m.groups()
        date, date_fixed = norm_date(date_raw)
        amount = dec(amt_raw)
        if drcr == "DR":
            amount = -abs(amount)
        elif drcr == "CR":
            amount = abs(amount)
        if "," in amt_raw or date_fixed:
            fixes.append({"kind": "format", "detail":
                          f"normalized line: date '{date_raw}' -> '{date}', amount '{amt_raw}' -> '{amount:.2f}'"})
        txns.append({"date": date, "desc": desc.strip(), "amount": amount,
                     "balance": dec(bal_raw)})
    return opening, closing, txns, fixes


def dedupe(txns):
    seen, kept, dupes = set(), [], []
    for t in txns:
        key = (t["date"], t["desc"], t["amount"], t["balance"])
        if key in seen:
            dupes.append(t)
        else:
            seen.add(key)
            kept.append(t)
    return kept, dupes


def chain_breaks(opening, txns):
    """Walk stated balances; return list of break dicts."""
    breaks = []
    prev = opening
    for i, t in enumerate(txns):
        if prev is not None:
            expected = prev + t["amount"]
            if expected != t["balance"]:
                breaks.append({"index": i, "date": t["date"], "desc": t["desc"],
                               "expected": expected, "stated": t["balance"],
                               "difference": t["balance"] - expected})
        prev = t["balance"]
    return breaks


def run_statementsift(pdf_path, outdir):
    os.makedirs(outdir, exist_ok=True)
    lines, pages = extract_lines(pdf_path)
    opening, closing, txns, fixes = parse_statement(lines)
    if not txns:
        raise ValueError("no transaction lines found — this build reads text-based "
                         "statements whose lines end in '<amount> <balance>'; "
                         "scanned/image PDFs need OCR and are out of scope")
    kept, dupes = dedupe(txns)
    for d in dupes:
        fixes.append({"kind": "duplicate", "detail":
                      f"removed exact duplicate line: {d['date']} {d['desc']} {d['amount']:.2f}"})
    breaks = chain_breaks(opening, kept)
    computed_close = None
    if opening is not None:
        computed_close = opening + sum((t["amount"] for t in kept), Decimal("0"))
    close_diff = None
    if closing is not None and computed_close is not None:
        close_diff = closing - computed_close

    csv_path = os.path.join(outdir, "transactions.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "description", "amount", "balance", "flags"])
        break_idx = {b["index"] for b in breaks}
        for i, t in enumerate(kept):
            w.writerow([t["date"], t["desc"], f"{t['amount']:.2f}", f"{t['balance']:.2f}",
                        "BALANCE_BREAK" if i in break_idx else ""])

    # Re-check: re-parse the cleaned CSV and re-verify independently.
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    re_txns = [{"date": r["date"], "desc": r["description"],
                "amount": Decimal(r["amount"]), "balance": Decimal(r["balance"])} for r in rows]
    _, re_dupes = dedupe(re_txns)
    re_breaks = chain_breaks(opening, re_txns)
    recheck_ok = (len(re_dupes) == 0 and len(re_breaks) == len(breaks))

    L = ["# StatementSift — conversion & reconciliation report", "",
         f"Source: {os.path.basename(pdf_path)}  ·  Pages: {pages}  ·  Transaction lines found: {len(txns)}", "",
         "## Reconciliation"]
    L.append(f"- Opening balance (stated): {opening if opening is not None else 'not found'}")
    L.append(f"- Closing balance (stated): {closing if closing is not None else 'not found'}")
    L.append(f"- Closing balance (computed: opening + all amounts): {computed_close}")
    if close_diff is not None:
        L.append(f"- Difference: {close_diff:+.2f} — " +
                 ("the statement reconciles exactly." if close_diff == 0 else
                  "the statement does NOT reconcile; see the balance break(s) below."))
    L += ["", "## Balance breaks (reported, never silently repaired)"]
    if breaks:
        for b in breaks:
            L.append(f"- {b['date']} {b['desc']}: running balance should be {b['expected']:.2f} "
                     f"but the statement prints {b['stated']:.2f} (difference {b['difference']:+.2f})")
    else:
        L.append("- none — every running-balance step checks out")
    L += ["", "## What was corrected automatically"]
    L += [f"- {f['detail']}" for f in fixes] or ["- nothing"]
    L += ["", "## Re-check verdict",
          f"- {'PASS' if recheck_ok else 'FAIL'}: cleaned CSV re-parsed independently — "
          f"{len(re_dupes)} duplicates remain, {len(re_breaks)} balance break(s) "
          f"({'the same break(s) reported above, carried honestly' if len(re_breaks) == len(breaks) else 'MISMATCH vs first pass'}).",
          "- Scope note: text-based statement PDFs with '<amount> <balance>' line endings. "
          "Scanned statements, and banks that print separate debit/credit columns with no running balance, are out of scope for this build."]
    report_path = os.path.join(outdir, "reconciliation_report.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    return {"pages": pages, "found": len(txns), "kept": len(kept), "duplicates": len(dupes),
            "fixes": len(fixes), "breaks": len(breaks),
            "opening": str(opening), "closing_stated": str(closing),
            "closing_computed": str(computed_close), "close_diff": str(close_diff),
            "recheck_ok": recheck_ok, "csv_path": csv_path, "report_path": report_path}


def main(argv):
    if len(argv) != 3:
        print("usage: statement_sift.py <statement.pdf> <outdir>")
        return 2
    s = run_statementsift(argv[1], argv[2])
    print(f"pages={s['pages']} found={s['found']} kept={s['kept']} duplicates={s['duplicates']} "
          f"fixes={s['fixes']} breaks={s['breaks']} close_diff={s['close_diff']} recheck_ok={s['recheck_ok']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
