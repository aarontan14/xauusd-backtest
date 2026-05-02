"""
Live trading bot — IBKR (ib_insync). Runs the v8 MacroTrend signals against live
gold data and sends orders to IBKR (TWS or IB Gateway).

REQUIREMENTS:
  pip install ib_insync yfinance pandas numpy
  IBKR TWS or IB Gateway running, API enabled
  Live or paper account funded with permissions for XAUUSD CFD or GC/MGC futures.

INSTRUMENT CHOICES (Singapore-accessible via IBKR):
  - "CFD"   : CFD on XAUUSD (best for retail SG, fractional ounces ok). Symbol XAUUSD.
  - "MGC"   : Micro Gold futures on COMEX (10 oz, ~$5/tick). Lower margin needs.
  - "GC"    : Full Gold futures on COMEX (100 oz, ~$10/tick). Larger.

USAGE:
  Paper test first:    TWS port 7497 (paper) or 7496 (live)
  python live_ibkr_bot.py --instrument CFD --account DUxxxxxx --leverage 1.0 --paper
  Cron / Task Scheduler this once daily after gold close (e.g. 06:00 SGT).

SAFETY:
  - DRY-RUN mode by default. Pass --live to actually send orders.
  - Sizes from equity * leverage; respects current position (no pyramiding).
  - Hard ATR stop set on entry.
"""
from __future__ import annotations
import argparse, json, logging, os, sys, time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

LOG = logging.getLogger("v8_bot")
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s")

# v8 locked parameters (from walk-forward selection)
PARAMS = {
    "ma_pull": 10, "ma_exit": 50, "ma_long": 200,
    "dxy_lb": 60, "tnx_lb": 60,
    "dxy_thr": 0.0, "tnx_thr": 0.0,
    "rsi_max": 50, "rsi_exit": 75,
    "pull_buf": 1.05, "stop_mult": 3.0,
}


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


def fetch_market_data(years: int = 2) -> pd.DataFrame:
    """Pull latest daily bars for gold + macro from yfinance."""
    import yfinance as yf
    end = datetime.utcnow()
    start = end - timedelta(days=years * 365)
    gold = yf.download("GC=F", start=start, end=end, interval="1d",
                       auto_adjust=False, progress=False)
    if isinstance(gold.columns, pd.MultiIndex):
        gold.columns = [c[0] for c in gold.columns]
    gold = gold[["Open","High","Low","Close"]].rename(columns=str.lower)
    out = gold
    for label, sym in [("dxy","DX-Y.NYB"), ("tnx","^TNX")]:
        s = yf.download(sym, start=start, end=end, interval="1d",
                        auto_adjust=False, progress=False)
        if isinstance(s.columns, pd.MultiIndex):
            s.columns = [c[0] for c in s.columns]
        out[label] = s["Close"].reindex(out.index).ffill()
    return out.dropna()


def compute_signal(df: pd.DataFrame) -> dict:
    """Return today's signal: {'action': 'buy'|'sell'|'flat', 'stop': float, 'reason': str}."""
    p = PARAMS
    out = df.copy()
    out["ma_pull"] = out["close"].rolling(p["ma_pull"]).mean()
    out["ma_exit"] = out["close"].rolling(p["ma_exit"]).mean()
    out["ma_long"] = out["close"].rolling(p["ma_long"]).mean()
    out["dxy_chg"] = out["dxy"].pct_change(p["dxy_lb"])
    out["tnx_chg"] = out["tnx"].diff(p["tnx_lb"])
    out["rsi"]     = rsi(out["close"], 14)
    out["atr"]     = atr(out, 14)

    macro = (out["dxy_chg"] < p["dxy_thr"]) & (out["tnx_chg"] < p["tnx_thr"]) & (out["close"] > out["ma_long"])
    pull  = (out["rsi"] < p["rsi_max"]) & (out["close"] <= out["ma_pull"] * p["pull_buf"])
    out["entry"] = macro & pull
    out["exit"]  = (out["close"] < out["ma_exit"]) | (out["rsi"] > p["rsi_exit"])

    last = out.iloc[-1]
    if bool(last["entry"]):
        stop = float(last["close"] - p["stop_mult"] * last["atr"])
        return {"action": "buy", "stop": stop, "atr": float(last["atr"]),
                "close": float(last["close"]), "reason": "macro_pullback"}
    if bool(last["exit"]):
        return {"action": "sell", "close": float(last["close"]), "reason": "regime_break"}
    return {"action": "flat", "close": float(last["close"]), "reason": "no_signal"}


# -------------------------------------------------------------------------
# IBKR execution
# -------------------------------------------------------------------------

def ibkr_execute(signal: dict, instrument: str, account: str | None,
                 host: str, port: int, client_id: int, leverage: float,
                 dry_run: bool):
    try:
        from ib_insync import IB, Forex, Future, CFD, MarketOrder, StopOrder, Contract
    except ImportError:
        LOG.error("ib_insync not installed. pip install ib_insync"); sys.exit(2)

    ib = IB()
    ib.connect(host, port, clientId=client_id)
    LOG.info(f"Connected to IBKR {host}:{port}")

    # Contract setup
    if instrument == "CFD":
        contract = CFD("XAUUSD", "SMART", "USD")
    elif instrument == "MGC":
        # Micro gold futures — needs current front-month
        contract = Future("MGC", exchange="COMEX", currency="USD")
        ib.qualifyContracts(contract)
    elif instrument == "GC":
        contract = Future("GC", exchange="COMEX", currency="USD")
        ib.qualifyContracts(contract)
    else:
        raise ValueError(f"Unknown instrument {instrument}")
    ib.qualifyContracts(contract)
    LOG.info(f"Contract: {contract}")

    # Get account equity
    summary = ib.accountSummary(account or "")
    nl = next((float(x.value) for x in summary if x.tag == "NetLiquidation"), None)
    if nl is None:
        LOG.error("Could not read NetLiquidation"); ib.disconnect(); return
    LOG.info(f"NetLiquidation = ${nl:,.0f}")

    # Current position
    positions = [p for p in ib.positions(account or "") if p.contract.conId == contract.conId]
    pos_qty = positions[0].position if positions else 0
    LOG.info(f"Current position qty = {pos_qty}")

    # Get last price
    [ticker] = ib.reqTickers(contract)
    last = ticker.marketPrice()
    LOG.info(f"Last price = {last}")

    if signal["action"] == "buy" and pos_qty == 0:
        notional = nl * leverage
        if instrument == "CFD":
            qty = round(notional / last, 2)  # fractional ok
        elif instrument == "MGC":
            qty = max(1, int(notional / (last * 10)))   # 10 oz contract
        else:  # GC
            qty = max(1, int(notional / (last * 100)))  # 100 oz contract
        LOG.info(f"BUY signal -> qty {qty} (notional ${qty*last*(10 if instrument=='MGC' else 100 if instrument=='GC' else 1):,.0f})")
        if dry_run:
            LOG.info("DRY RUN — order not sent.")
        else:
            entry = MarketOrder("BUY", qty, account=account)
            stop  = StopOrder("SELL", qty, signal["stop"], account=account)
            t1 = ib.placeOrder(contract, entry)
            ib.sleep(2)
            t2 = ib.placeOrder(contract, stop)
            LOG.info(f"Sent entry {t1} and stop {t2}")

    elif signal["action"] == "sell" and pos_qty > 0:
        LOG.info(f"SELL signal -> closing {pos_qty}")
        if dry_run:
            LOG.info("DRY RUN — order not sent.")
        else:
            ib.cancelAllOpenOrders()
            ib.placeOrder(contract, MarketOrder("SELL", pos_qty, account=account))
    else:
        LOG.info(f"No action. signal={signal['action']} pos={pos_qty}")

    ib.disconnect()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", choices=["CFD","MGC","GC"], default="CFD")
    ap.add_argument("--account", default=None, help="IBKR account ID")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497, help="7497=paper TWS, 7496=live TWS, 4002=paper gateway, 4001=live gateway")
    ap.add_argument("--client-id", type=int, default=42)
    ap.add_argument("--leverage", type=float, default=1.0)
    ap.add_argument("--live", action="store_true", help="Send real orders (default = dry run)")
    args = ap.parse_args()

    LOG.info("Fetching market data ...")
    df = fetch_market_data(years=2)
    LOG.info(f"Latest bar: {df.index[-1].date()}  close={df['close'].iloc[-1]:.2f}")

    sig = compute_signal(df)
    LOG.info(f"Signal: {json.dumps(sig)}")

    ibkr_execute(sig, args.instrument, args.account, args.host, args.port,
                 args.client_id, args.leverage, dry_run=not args.live)


if __name__ == "__main__":
    main()
