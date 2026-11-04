#!/usr/bin/env python3
"""StatementSift desktop shell (tkinter).
Pick a bank statement PDF -> clean transactions CSV + reconciliation report."""
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from statement_sift import run_batch, run_statementsift


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("StatementSift — Statement PDF to CSV, Reconciled")
        self.geometry("680x520")
        self.src = tk.StringVar()
        tk.Label(self, text="Bank statement PDF (text-based, downloaded from your bank):").pack(anchor="w", padx=12, pady=(12, 2))
        row = tk.Frame(self)
        row.pack(fill="x", padx=12)
        tk.Entry(row, textvariable=self.src).pack(side="left", fill="x", expand=True)
        tk.Button(row, text="Browse…", command=self.pick).pack(side="left", padx=6)
        self.btn = tk.Button(self, text="Convert & Reconcile", command=self.run, height=2)
        self.btn.pack(pady=12)
        tk.Label(self, text="…or a whole folder of statement PDFs — e.g. 12 months / several accounts (batch):").pack(anchor="w", padx=12, pady=(2, 2))
        self.folder = tk.StringVar()
        row2 = tk.Frame(self)
        row2.pack(fill="x", padx=12)
        tk.Entry(row2, textvariable=self.folder).pack(side="left", fill="x", expand=True)
        tk.Button(row2, text="Browse folder…", command=self.pick_folder).pack(side="left", padx=6)
        self.btn_batch = tk.Button(self, text="Convert folder (batch) — merged CSV + cross-period report",
                                   command=self.run_batch_gui, height=2)
        self.btn_batch.pack(pady=12)
        self.log = scrolledtext.ScrolledText(self, wrap="word")
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.write("StatementSift converts a statement PDF into a clean CSV, removes exact\n"
                   "duplicate lines, and then reconciles every running-balance step plus the\n"
                   "closing balance. Breaks are reported with expected vs printed values.\n"
                   "Everything runs locally — your bank statement never leaves this computer.\n")

    def write(self, s):
        self.log.insert("end", s)
        self.log.see("end")

    def pick(self):
        p = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")])
        if p:
            self.src.set(p)

    def pick_folder(self):
        p = filedialog.askdirectory()
        if p:
            self.folder.set(p)

    def run_batch_gui(self):
        folder = self.folder.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("StatementSift", "Choose a folder of statement PDFs first.")
            return
        self.btn_batch.config(state="disabled")
        threading.Thread(target=self._work_batch, args=(folder,), daemon=True).start()

    def _work_batch(self, folder):
        try:
            outdir = os.path.join(os.path.abspath(folder), "statementsift_batch_output")
            s = run_batch(folder, outdir)
            self.write(f"\nBatch done. {s['processed']} of {s['files_found']} PDFs processed "
                       f"({s['failed']} failed), {s['total_txns']} transactions merged across "
                       f"{s['periods']} periods.\n"
                       f"Cross-period link gaps: {s['link_gaps']}  ·  "
                       f"Grand reconciliation: {'PASS' if s['grand_ok'] else 'FAIL'}  ·  "
                       f"Re-check: {'PASS' if s['recheck_ok'] else 'FAIL'}\n"
                       f"Merged CSV: {s['merged_csv_path']}\nCross-period report: {s['report_path']}\n")
        except Exception as e:
            self.write(f"\nError: {e}\n")
        finally:
            self.btn_batch.config(state="normal")

    def run(self):
        src = self.src.get().strip()
        if not src or not os.path.isfile(src):
            messagebox.showerror("StatementSift", "Choose a statement PDF first.")
            return
        self.btn.config(state="disabled")
        threading.Thread(target=self._work, args=(src,), daemon=True).start()

    def _work(self, src):
        try:
            outdir = os.path.join(os.path.dirname(os.path.abspath(src)), "statementsift_output")
            s = run_statementsift(src, outdir)
            self.write(f"\nDone. {s['found']} transaction lines found, {s['kept']} kept "
                       f"({s['duplicates']} exact duplicates removed, {s['fixes']} corrections logged).\n"
                       f"Balance breaks: {s['breaks']}  ·  Closing difference: {s['close_diff']}  ·  "
                       f"Re-check: {'PASS' if s['recheck_ok'] else 'FAIL'}\n"
                       f"CSV: {s['csv_path']}\nReport: {s['report_path']}\n")
        except Exception as e:
            self.write(f"\nError: {e}\n")
        finally:
            self.btn.config(state="normal")


if __name__ == "__main__":
    App().mainloop()
