#!/usr/bin/env python3
"""StatementSift desktop shell (tkinter).
Pick a bank statement PDF -> clean transactions CSV + reconciliation report."""
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from statement_sift import run_statementsift


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
