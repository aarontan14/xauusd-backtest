"""
Monthly health check for the v8 (gold) and C6 (BTC/ETH) strategies. Runs locally.

What it does:
  1. Re-fetches fresh daily data via yfinance:
       - Gold + macro: GC=F, DX-Y.NYB, ^TNX, ^VIX, ^GSPC
       - Crypto:       BTC-USD, ETH-USD
     and overwrites data/*_daily.csv.
  2. Runs each locked strategy at 1.0x leverage on the full sample.
     Also runs OOS / trailing windows.
  3. Compares to the LOCKED BASELINE per asset and flags regime drift.
  4. Writes a timestamped report to logs/monthly_YYYY-MM-DD.txt and prints to stdout.
"""
from __future__ import annotations
import os, sys, traceback
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import pandas as pd
import numpy as np

# --------------------------------------------------------------------------
# Locked baselines per asset (full sample, 1x leverage)
# --------------------------------------------------------------------------
BASELINES = {
    "XAUUSD_v8": {"cagr": 0.0714, "sharpe": 1.27, "max_dd": -0.069, "calmar": 1.03},
    # Crypto v2 (vol-targeted, margin-mode params: BTC lev_cap=3, ETH lev_cap=2 + 3*ATR stop)
    "BTC_C6v2":  {"cagr": 0.7930, "sharpe": 1.54, "max_dd": -0.421, "calmar": 1.88},
    "ETH_C6v2":  {"cagr": 0.5155, "sharpe": 1.14, "max_dd": -0.393, "calmar": 1.31},
}

# Per-asset regime-warning thresholds. Wider for crypto because crypto runs at
# structurally higher vol and DDs.
THRESHOLDS = {
    "XAUUSD_v8": {"sharpe_min": 0.80, "max_dd_min": -0.12, "trailing_cagr_min": 0.0},
    "BTC_C6v2":  {"sharpe_min": 0.95, "max_dd_min": -0.55, "trailing_cagr_min": 0.0},
    "ETH_C6v2":  {"sharpe_min": 0.70, "max_dd_min": -0.50, "trailing_cagr_min": 0.0},
}


# --------------------------------------------------------------------------
# Data refresh
# --------------------------------------------------------------------------

def refresh_data(log):
    import yfinance as yf
    out_dir = os.path.join(ROOT, "data")
    os.makedirs(out_dir, exist_ok=True)
    tickers = {
        "XAUUSD_daily.csv": ("GC=F",     "2010-01-01"),
        "DXY_daily.csv":    ("DX-Y.NYB", "2010-01-01"),
        "TNX_daily.csv":    ("^TNX",     "2010-01-01"),
        "VIX_daily.csv":    ("^VIX",     "2010-01-01"),
        "SPX_daily.csv":    ("^GSPC",    "2010-01-01"),
        "BTC_daily.csv":    ("BTC-USD",  "2017-01-01"),
        "ETH_daily.csv":    ("ETH-USD",  "2017-01-01"),
    }
    end = datetime.utcnow().date().isoformat()
    for fname, (sym, start) in tickers.items():
        df = yf.download(sym, start=start, end=end, interval="1d",
                         auto_adjust=False, progress=False)
        if len(df) == 0:
            log(f"  WARN: {sym} returned 0 rows")
            continue
        df.to_csv(os.path.join(out_dir, fname))
        log(f"  fetched {sym:9s} -> {fname:20s}: {len(df):4d} rows  "
            f"({df.index.min().date()} -> {df.index.max().date()})")


# --------------------------------------------------------------------------
# Per-strategy runners
# --------------------------------------------------------------------------

def run_xauusd():
    from final_v8 import run_realistic
    from backtest import load_data, buy_hold

    df = load_data()
    _eq, _tr, m_full = run_realistic(df, leverage=1.0)
    _eq, _tr, m_oos  = run_realistic(df.loc["2019-01-01":], leverage=1.0)
    df_24 = df.loc["2024-01-01":]
    m_24 = run_realistic(df_24, leverage=1.0)[2] if len(df_24) > 250 else {}
    bh = buy_hold(df)["metrics"]
    bh["calmar"] = bh["cagr"] / abs(bh["max_dd"]) if bh["max_dd"] else 0
    return {
        "data_first": df.index.min().date().isoformat(),
        "data_last":  df.index.max().date().isoformat(),
        "n_bars":     len(df),
        "full":       m_full,
        "oos":        m_oos,
        "trailing":   m_24,
        "trailing_label": "2024-now",
        "buy_hold":   bh,
    }


def run_crypto_v2(asset: str):
    """asset in {'BTC','ETH'}. Uses C6+RC v2: vol target + leverage cap + optional hard stop."""
    from crypto_backtest_v2 import load_crypto, signals_c6, run, buy_hold
    from final_crypto_v2 import PARAMS

    cfg = PARAMS[asset]
    df = load_crypto(asset)
    sig = signals_c6(df, **cfg["signal"])
    risk = cfg["risk_margin"]   # use margin params as the "production" baseline

    m_full = run(sig, **risk)["metrics"]
    m_oos  = run(sig.loc["2023-01-01":], **risk)["metrics"]

    if len(df.loc["2024-01-01":]) > 250:
        m_24 = run(sig.loc["2024-01-01":], **risk)["metrics"]
    else:
        m_24 = {}

    bh = buy_hold(df)["metrics"]
    bh["calmar"] = bh["cagr"] / abs(bh["max_dd"]) if bh["max_dd"] else 0
    return {
        "data_first": df.index.min().date().isoformat(),
        "data_last":  df.index.max().date().isoformat(),
        "n_bars":     len(df),
        "full":       m_full,
        "oos":        m_oos,
        "trailing":   m_24,
        "trailing_label": "2024-now",
        "buy_hold":   bh,
    }


# --------------------------------------------------------------------------
# Regime evaluation + rendering
# --------------------------------------------------------------------------

def evaluate(strategy_id: str, metrics: dict):
    base = BASELINES[strategy_id]
    th   = THRESHOLDS[strategy_id]
    full = metrics["full"]

    warns = []
    if full["sharpe"] < th["sharpe_min"]:
        warns.append(f"Sharpe below {th['sharpe_min']:.2f} (now {full['sharpe']:.2f})")
    if full["max_dd"] < th["max_dd_min"]:
        warns.append(f"MaxDD breached {th['max_dd_min']:.0%} (now {full['max_dd']:.2%})")
    if metrics["trailing"] and metrics["trailing"].get("cagr", 0) < th["trailing_cagr_min"]:
        warns.append(f"{metrics['trailing_label']} trailing CAGR negative ({metrics['trailing']['cagr']:.2%})")
    critical = len(warns) >= 2
    return warns, critical, base


def render_block(strategy_id: str, asset_label: str, metrics: dict,
                 warns: list, critical: bool, base: dict) -> str:
    full = metrics["full"]
    oos  = metrics["oos"]
    bh   = metrics["buy_hold"]
    L = []
    L.append(f"--- {asset_label} ({strategy_id}) ---")
    L.append(f"  Data: {metrics['data_first']} -> {metrics['data_last']}  ({metrics['n_bars']} bars)")
    L.append(f"  Full sample @ 1.0x:")
    L.append(f"    CAGR   : {full['cagr']:>7.2%}   (baseline {base['cagr']:.2%}, drift {full['cagr']-base['cagr']:+.2%})")
    L.append(f"    Sharpe : {full['sharpe']:>7.2f}    (baseline {base['sharpe']:.2f},  drift {full['sharpe']-base['sharpe']:+.2f})")
    L.append(f"    MaxDD  : {full['max_dd']:>7.2%}   (baseline {base['max_dd']:.1%}, drift {full['max_dd']-base['max_dd']:+.2%})")
    L.append(f"    Calmar : {full['calmar']:>7.2f}    (baseline {base['calmar']:.2f})")
    L.append(f"    Trades : {int(full['n_trades'])}")
    L.append(f"  OOS slice: CAGR {oos['cagr']:.2%}  Sharpe {oos['sharpe']:.2f}  MaxDD {oos['max_dd']:.2%}")
    L.append(f"  Buy-hold:  CAGR {bh['cagr']:.2%}  Sharpe {bh['sharpe']:.2f}  MaxDD {bh['max_dd']:.2%}")
    if not warns:
        L.append(f"  Status: [OK] Edge intact.")
    elif critical:
        L.append(f"  Status: [CRITICAL] {len(warns)} thresholds breached: " + "; ".join(warns))
    else:
        L.append(f"  Status: [WARN] " + "; ".join(warns))
    return "\n".join(L)


def render_report(blocks: list[str], any_critical: bool, any_warn: bool) -> str:
    today = datetime.utcnow().date().isoformat()
    L = [f"=== STRATEGY MONTHLY HEALTH CHECK - {today} ==="]
    L.append("")
    L.extend(blocks)
    L.append("")
    L.append("=== OVERALL RECOMMENDATION ===")
    if any_critical:
        L.append("HALT live trading on any [CRITICAL] strategies. Re-tune via the corresponding")
        L.append("tune_*.py script before redeploying. Continue trading any [OK] strategies.")
    elif any_warn:
        L.append("Reduce position sizing on [WARN] strategies (drop to 1.0x if leveraged), and")
        L.append("monitor closely. Single threshold breaches can be transient — confirm next month.")
    else:
        L.append("All strategies healthy. Continue trading locked parameters at 1.0x-1.5x leverage.")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

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

        log("\nRunning backtests ...")
        results = {
            ("XAUUSD_v8", "XAU/USD v8 MacroTrend"):       run_xauusd(),
            ("BTC_C6v2",  "BTC C6+RC v2 VolBreakout"):    run_crypto_v2("BTC"),
            ("ETH_C6v2",  "ETH C6+RC v2 VolBreakout"):    run_crypto_v2("ETH"),
        }

        blocks = []
        any_critical = any_warn = False
        for (sid, label), metrics in results.items():
            warns, critical, base = evaluate(sid, metrics)
            blocks.append(render_block(sid, label, metrics, warns, critical, base))
            if critical: any_critical = True
            if warns:    any_warn = True

        report = render_report(blocks, any_critical, any_warn)
        log("\n" + report)

    except Exception:
        tb = traceback.format_exc()
        log(f"\nFAILED:\n{tb}")

    with open(logfile, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log(f"\nLog written to {logfile}")


if __name__ == "__main__":
    main()
