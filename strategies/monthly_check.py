"""
Monthly health check for the v8 MacroTrend strategy. Runs locally.

What it does:
  1. Re-fetches fresh daily data via yfinance (XAUUSD/GC=F, DXY, TNX, VIX, SPX)
     and overwrites data/*_daily.csv.
  2. Runs the locked v8 backtest at 1.0x leverage with cash yield, full sample.
  3. Also runs the OOS slice (2019-now) and a buy-and-hold benchmark.
  4. Compares to the LOCKED BASELINE and flags regime drift if thresholds breached.
  5. Writes a timestamped report to logs/monthly_YYYY-MM-DD.txt and prints to stdout.

Locked baseline (from the original backtest, 2010-2026, 1x leverage, +4% cash yield):
  CAGR    = 7.14%
  Sharpe  = 1.27
  MaxDD   = -6.9%
  Calmar  = 1.03

Warning thresholds:
  - Sharpe drops below 0.80
  - MaxDD breaches -12.0%
  - Trailing 2024+ window CAGR turns negative
"""
from __future__ import annotations
import os, sys, json, traceback
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import pandas as pd
import numpy as np

# Locked baseline & thresholds
BASELINE = {"cagr": 0.0714, "sharpe": 1.27, "max_dd": -0.069, "calmar": 1.03}
THRESHOLDS = {"sharpe_min": 0.80, "max_dd_min": -0.12, "trailing_cagr_min": 0.0}


def refresh_data(log):
    import yfinance as yf
    out_dir = os.path.join(ROOT, "data")
    os.makedirs(out_dir, exist_ok=True)
    tickers = {
        "XAUUSD_daily.csv": "GC=F",
        "DXY_daily.csv":    "DX-Y.NYB",
        "TNX_daily.csv":    "^TNX",
        "VIX_daily.csv":    "^VIX",
        "SPX_daily.csv":    "^GSPC",
    }
    end = datetime.utcnow().date().isoformat()
    for fname, sym in tickers.items():
        df = yf.download(sym, start="2010-01-01", end=end, interval="1d",
                         auto_adjust=False, progress=False)
        if len(df) == 0:
            log(f"WARN: {sym} returned 0 rows")
            continue
        path = os.path.join(out_dir, fname)
        df.to_csv(path)
        log(f"  fetched {sym} -> {fname}: {len(df)} rows  ({df.index.min().date()} -> {df.index.max().date()})")


def run_check():
    from final_v8 import run_realistic
    from backtest import load_data, buy_hold

    df = load_data()
    full = run_realistic(df, leverage=1.0)
    eq_full, tr_full, m_full = full

    df_oos = df.loc["2019-01-01":]
    eq_oos, tr_oos, m_oos = run_realistic(df_oos, leverage=1.0)

    df_2024 = df.loc["2024-01-01":]
    eq_24, tr_24, m_24 = run_realistic(df_2024, leverage=1.0) if len(df_2024) > 250 else (None, None, {})

    bh = buy_hold(df)["metrics"]
    bh_calmar = bh["cagr"] / abs(bh["max_dd"]) if bh["max_dd"] else 0

    return {
        "data_first": df.index.min().date().isoformat(),
        "data_last":  df.index.max().date().isoformat(),
        "n_bars":     len(df),
        "full":       m_full,
        "oos":        m_oos,
        "trailing24": m_24,
        "buy_hold":   {**bh, "calmar": bh_calmar},
    }


def evaluate_regime(metrics):
    warns, critical = [], False
    full = metrics["full"]
    if full["sharpe"] < THRESHOLDS["sharpe_min"]:
        warns.append(f"[WARN] Sharpe below {THRESHOLDS['sharpe_min']:.2f} (now {full['sharpe']:.2f}). Possible regime drift.")
    if full["max_dd"] < THRESHOLDS["max_dd_min"]:
        warns.append(f"[WARN] MaxDD breached {THRESHOLDS['max_dd_min']:.0%} (now {full['max_dd']:.2%}). Risk profile degraded.")
    if metrics["trailing24"] and metrics["trailing24"].get("cagr", 0) < THRESHOLDS["trailing_cagr_min"]:
        warns.append(f"[WARN] 2024+ trailing CAGR negative ({metrics['trailing24']['cagr']:.2%}). Recent performance poor.")
    if len(warns) >= 2:
        critical = True
    return warns, critical


def render_report(metrics, warns, critical) -> str:
    full = metrics["full"]
    oos  = metrics["oos"]
    bh   = metrics["buy_hold"]
    today = datetime.utcnow().date().isoformat()
    L = []
    L.append(f"=== XAUUSD v8 MONTHLY HEALTH CHECK - {today} ===\n")
    L.append(f"Data window: {metrics['data_first']} -> {metrics['data_last']}  ({metrics['n_bars']} bars)\n")
    L.append("Full sample @ 1.0x leverage:")
    L.append(f"  CAGR    : {full['cagr']:.2%}    (baseline 7.14%, drift {full['cagr']-BASELINE['cagr']:+.2%})")
    L.append(f"  Sharpe  : {full['sharpe']:.2f}     (baseline 1.27,  drift {full['sharpe']-BASELINE['sharpe']:+.2f})")
    L.append(f"  MaxDD   : {full['max_dd']:.2%}    (baseline -6.9%, drift {full['max_dd']-BASELINE['max_dd']:+.2%})")
    L.append(f"  Calmar  : {full['calmar']:.2f}     (baseline 1.03)")
    L.append(f"  Trades  : {int(full['n_trades'])}")
    L.append("")
    L.append(f"Out-of-sample 2019-now @ 1.0x:")
    L.append(f"  CAGR {oos['cagr']:.2%}  Sharpe {oos['sharpe']:.2f}  MaxDD {oos['max_dd']:.2%}")
    L.append("")
    L.append("Buy-and-hold benchmark (full sample):")
    L.append(f"  CAGR {bh['cagr']:.2%}  Sharpe {bh['sharpe']:.2f}  MaxDD {bh['max_dd']:.2%}")
    L.append("")
    L.append("=== REGIME STATUS ===")
    if not warns:
        L.append("[OK] Strategy edge intact. No thresholds breached.")
    else:
        if critical:
            L.append(f"[CRITICAL] Multiple thresholds breached:")
        for w in warns:
            L.append(f"  {w}")
    L.append("")
    L.append("=== RECOMMENDATION ===")
    if not warns:
        L.append("Continue trading the locked v8 parameters at 1.0x-1.5x leverage. Edge intact.")
    elif critical:
        L.append("HALT live trading. Multiple breach signals — likely a regime shift in gold's macro driver. Re-run tune_v8.py to refit, then evaluate whether to redeploy.")
    else:
        L.append("Reduce position sizing (drop to 1.0x if running leveraged) and monitor closely. One threshold breached — could be a transient drawdown, but watch next month's check.")
    return "\n".join(L) + "\n"


def main():
    log_dir = os.path.join(ROOT, "logs")
    os.makedirs(log_dir, exist_ok=True)
    logfile = os.path.join(log_dir, f"monthly_{datetime.utcnow().date().isoformat()}.txt")

    lines = []
    def log(msg):
        print(msg, flush=True)
        lines.append(str(msg))

    log(f"[{datetime.utcnow().isoformat()}Z] Monthly check starting")
    try:
        log("Refreshing market data ...")
        refresh_data(log)
        log("Running v8 backtest ...")
        metrics = run_check()
        warns, critical = evaluate_regime(metrics)
        report = render_report(metrics, warns, critical)
        log("\n" + report)
    except Exception:
        tb = traceback.format_exc()
        log(f"\nFAILED:\n{tb}")
        report = f"=== XAUUSD v8 MONTHLY HEALTH CHECK - FAILED {datetime.utcnow().date()} ===\n\n{tb}"

    with open(logfile, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log(f"\nLog written to {logfile}")


if __name__ == "__main__":
    main()
