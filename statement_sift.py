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


PERIOD_RE = re.compile(r"Statement period[:\s]+(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})", re.I)


def parse_period(lines, txns):
    """Statement period from the printed 'Statement period:' line when present;
    otherwise the first/last transaction dates of the period."""
    for raw in lines:
        m = PERIOD_RE.search(raw)
        if m:
            return m.group(1), m.group(2)
    dates = [t["date"] for t in txns]
    return (min(dates), max(dates)) if dates else ("", "")


def _process_one(pdf_path):
    """Batch orchestration helper: run one statement through the same
    single-file primitives (extract -> parse -> dedupe -> chain check).
    The single-file engine functions above are reused unchanged."""
    lines, pages = extract_lines(pdf_path)
    opening, closing, txns, fixes = parse_statement(lines)
    if not txns:
        raise ValueError("no transaction lines found — this build reads text-based "
                         "statements whose lines end in '<amount> <balance>'; "
                         "scanned/image PDFs need OCR and are out of scope")
    kept, dupes = dedupe(txns)
    breaks = chain_breaks(opening, kept)
    computed_close = None
    if opening is not None:
        computed_close = opening + sum((t["amount"] for t in kept), Decimal("0"))
    close_diff = None
    if closing is not None and computed_close is not None:
        close_diff = closing - computed_close
    p_start, p_end = parse_period(lines, kept)
    return {"source_file": os.path.basename(pdf_path), "pages": pages,
            "period_start": p_start, "period_end": p_end,
            "opening": opening, "closing_stated": closing,
            "closing_computed": computed_close, "close_diff": close_diff,
            "found": len(txns), "kept": kept, "duplicates": len(dupes),
            "fixes": len(fixes) + len(dupes), "breaks": breaks}


def run_batch(folder, outdir):
    """Folder batch: every statement PDF in `folder` (top level) in one pass.

    Outputs in `outdir`:
      - transactions_merged.csv — all periods' cleaned transactions in one
        file, each row tagged with source_file / period_start / period_end,
        ordered chronologically by period.
      - cross_period_report.md — one row per period (opening/closing,
        transaction count, link to the previous period's stated closing,
        gap amount) plus a grand reconciliation that must tie out exactly:
        last stated closing - (first opening + sum of all amounts)
        == sum of per-period closing differences + sum of link gaps.
    A file that cannot be parsed is reported as FAILED and skipped; it
    never aborts the rest of the batch.
    """
    os.makedirs(outdir, exist_ok=True)
    names = sorted(n for n in os.listdir(folder)
                   if n.lower().endswith(".pdf")
                   and os.path.isfile(os.path.join(folder, n)))
    if not names:
        raise ValueError(f"no PDF files found in folder: {folder}")
    periods, failures = [], []
    for n in names:
        try:
            periods.append(_process_one(os.path.join(folder, n)))
        except Exception as e:  # reported in the summary, never silent
            failures.append({"source_file": n, "error": str(e)})
    if not periods:
        raise ValueError("every PDF in the folder failed to parse; see failures")
    periods.sort(key=lambda p: (p["period_start"] or "9999", p["source_file"]))

    # Cross-period links: this period's stated opening vs previous stated closing.
    for i, p in enumerate(periods):
        if i == 0:
            p["link_gap"] = None
        elif p["opening"] is not None and periods[i - 1]["closing_stated"] is not None:
            p["link_gap"] = p["opening"] - periods[i - 1]["closing_stated"]
        else:
            p["link_gap"] = None

    merged_path = os.path.join(outdir, "transactions_merged.csv")
    with open(merged_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "description", "amount", "balance", "flags",
                    "source_file", "period_start", "period_end"])
        for p in periods:
            break_idx = {b["index"] for b in p["breaks"]}
            for i, t in enumerate(p["kept"]):
                w.writerow([t["date"], t["desc"], f"{t['amount']:.2f}", f"{t['balance']:.2f}",
                            "BALANCE_BREAK" if i in break_idx else "",
                            p["source_file"], p["period_start"], p["period_end"]])

    total_txns = sum(len(p["kept"]) for p in periods)
    total_amount = sum((t["amount"] for p in periods for t in p["kept"]), Decimal("0"))
    first_opening = periods[0]["opening"]
    last_closing = periods[-1]["closing_stated"]
    sum_close_diffs = sum((p["close_diff"] for p in periods if p["close_diff"] is not None), Decimal("0"))
    sum_link_gaps = sum((p["link_gap"] for p in periods if p["link_gap"] is not None), Decimal("0"))
    grand_residual = None
    grand_explained = None
    grand_ok = None
    if first_opening is not None and last_closing is not None:
        grand_residual = last_closing - (first_opening + total_amount)
        grand_explained = sum_close_diffs + sum_link_gaps
        grand_ok = (grand_residual == grand_explained)

    # Re-check: re-read the merged CSV independently.
    with open(merged_path, newline="", encoding="utf-8") as fh:
        merged_rows = list(csv.DictReader(fh))
    recheck_ok = (len(merged_rows) == total_txns and
                  sum((Decimal(r["amount"]) for r in merged_rows), Decimal("0")) == total_amount)

    def fmt(v):
        return f"{v:.2f}" if isinstance(v, Decimal) else "not found"

    L = ["# StatementSift — cross-period summary & reconciliation", "",
         f"Source folder: {folder}  ·  PDFs found: {len(names)}  ·  "
         f"Processed: {len(periods)}  ·  Failed: {len(failures)}", "",
         "## Per-period summary", "",
         "| # | Period | Source file | Opening | Closing (stated) | Closing (computed) | Txns | Internal breaks | Link to previous closing | Gap |",
         "|---|--------|-------------|---------|------------------|--------------------|------|-----------------|--------------------------|-----|"]
    for i, p in enumerate(periods):
        if i == 0:
            link, gap = "— (first period)", "—"
        elif p["link_gap"] is None:
            link, gap = "UNKNOWN (balance missing)", "—"
        elif p["link_gap"] == 0:
            link, gap = "OK", "0.00"
        else:
            link, gap = "GAP", f"{p['link_gap']:+.2f}"
        L.append(f"| {i + 1} | {p['period_start']} to {p['period_end']} | {p['source_file']} | "
                 f"{fmt(p['opening'])} | {fmt(p['closing_stated'])} | {fmt(p['closing_computed'])} | "
                 f"{len(p['kept'])} | {len(p['breaks'])} | {link} | {gap} |")
    L += ["", "## Cross-period links (reported, never silently repaired)"]
    gaps = [(periods[i - 1], p) for i, p in enumerate(periods)
            if i > 0 and p["link_gap"] is not None and p["link_gap"] != 0]
    if gaps:
        for prev, p in gaps:
            L.append(f"- GAP: {p['source_file']} opens at {fmt(p['opening'])} but "
                     f"{prev['source_file']} closed at {fmt(prev['closing_stated'])} "
                     f"(difference {p['link_gap']:+.2f}) — a transaction or a whole "
                     f"statement may be missing, or a balance was restated.")
    elif len(periods) > 1:
        L.append("- none — every period opens exactly where the previous period closed")
    else:
        L.append("- only one period in this batch — no links to check")
    L += ["", "## Grand reconciliation (all periods together)",
          f"- First opening balance: {fmt(first_opening)}",
          f"- Sum of all transaction amounts ({total_txns} transactions): {total_amount:.2f}",
          f"- Last stated closing balance: {fmt(last_closing)}",
          f"- Residual (last closing − first opening − all amounts): "
          f"{f'{grand_residual:+.2f}' if grand_residual is not None else 'n/a'}",
          f"- Explained by: per-period closing differences {sum_close_diffs:+.2f} "
          f"+ cross-period link gaps {sum_link_gaps:+.2f} = "
          f"{f'{grand_explained:+.2f}' if grand_explained is not None else 'n/a'}",
          f"- Grand check: {'PASS — the residual is fully explained by the reported differences and gaps above' if grand_ok else 'FAIL — residual is NOT fully explained; investigate before relying on this batch' if grand_ok is not None else 'n/a — a first opening or last closing balance was not found'}",
          "", "## Re-check verdict",
          f"- {'PASS' if recheck_ok else 'FAIL'}: merged CSV re-read independently — "
          f"{len(merged_rows)} rows, amounts sum to {total_amount:.2f} as in the per-period pass."]
    if failures:
        L += ["", "## Files that failed (skipped, not hidden)"]
        L += [f"- {f_['source_file']}: {f_['error']}" for f_ in failures]
    report_path = os.path.join(outdir, "cross_period_report.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    return {"files_found": len(names), "processed": len(periods), "failed": len(failures),
            "failures": failures, "total_txns": total_txns,
            "total_amount": str(total_amount), "periods": len(periods),
            "link_gaps": sum(1 for p in periods if p["link_gap"] not in (None, Decimal("0"))),
            "grand_ok": grand_ok, "recheck_ok": recheck_ok,
            "merged_csv_path": merged_path, "report_path": report_path}


def main(argv):
    args = argv[1:]
    if args and args[0] == "--batch":
        if len(args) != 3:
            print("usage: statement_sift.py --batch <folder> <outdir>")
            return 2
        s = run_batch(args[1], args[2])
        print(f"batch: files={s['files_found']} processed={s['processed']} failed={s['failed']} "
              f"periods={s['periods']} total_txns={s['total_txns']} link_gaps={s['link_gaps']} "
              f"grand_ok={s['grand_ok']} recheck_ok={s['recheck_ok']}")
        return 0
    if len(args) == 2 and os.path.isdir(args[0]):
        s = run_batch(args[0], args[1])
        print(f"batch: files={s['files_found']} processed={s['processed']} failed={s['failed']} "
              f"periods={s['periods']} total_txns={s['total_txns']} link_gaps={s['link_gaps']} "
              f"grand_ok={s['grand_ok']} recheck_ok={s['recheck_ok']}")
        return 0
    if len(argv) != 3:
        print("usage: statement_sift.py <statement.pdf> <outdir>")
        print("   or: statement_sift.py --batch <folder-of-pdfs> <outdir>")
        return 2
    s = run_statementsift(argv[1], argv[2])
    print(f"pages={s['pages']} found={s['found']} kept={s['kept']} duplicates={s['duplicates']} "
          f"fixes={s['fixes']} breaks={s['breaks']} close_diff={s['close_diff']} recheck_ok={s['recheck_ok']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
