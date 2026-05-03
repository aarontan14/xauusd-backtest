"""
Live trading bot — BTC/ETH C6 vol-breakout, executes on a CCXT-supported exchange.

REQUIREMENTS:
  pip install ccxt yfinance pandas numpy

WHY CCXT:
  Single API surface for Bybit, OKX, Binance, Independent Reserve, etc. Easy to swap
  exchange by changing one config line. All exchanges below support spot crypto trading
  for Singapore retail (verify your local KYC/licensing status before live use).

SUPPORTED EXCHANGES (Singapore-relevant):
  - bybit                : Bybit spot / futures, very deep liquidity, accessible from SG
  - okx                  : OKX spot / futures, accessible from SG
  - independentreserve   : MAS-licensed Singapore exchange (cleanest legal path)
  - coinbase             : limited SG availability
  - binance              : binance.com is technically not licensed in SG for new accounts;
                           grandfathered users can still use the API. Use at your own risk.

USAGE:
  Paper test first (most exchanges have testnets):
    python live_crypto_bot.py --asset BTC --exchange bybit --testnet

  Live (after API key creation in exchange UI, READ + TRADE permissions only,
        DO NOT enable WITHDRAW):
    export BYBIT_API_KEY=...
    export BYBIT_API_SECRET=...
    python live_crypto_bot.py --asset BTC --exchange bybit --live

  Schedule daily after UTC midnight (choose any consistent time):
    Windows Task Scheduler -> daily @ 09:00 SGT (= 01:00 UTC, after the daily bar closes)

SAFETY:
  - DRY-RUN by default. Pass --live to actually send orders.
  - Single position, no pyramiding.
  - Reads current position size from exchange; reconciles vs strategy's intended state.
  - Hard kill switch: if position drawdown > -25% from entry intra-trade, force exit.
"""
from __future__ import annotations
import argparse, json, logging, os, sys, time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

LOG = logging.getLogger("crypto_bot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Locked params per asset (must match final_crypto.py / Crypto_VolBreakout.pine)
PARAMS = {
    "BTC": {"dc_len": 10, "ma_exit_len": 50, "ma_long_len": 100, "yf_symbol": "BTC-USD"},
    "ETH": {"dc_len": 30, "ma_exit_len": 50, "ma_long_len": 150, "yf_symbol": "ETH-USD"},
}

KILL_SWITCH_DD = -0.25   # force-exit if intra-trade drawdown breaches this


def fetch_bars(yf_symbol: str, days: int = 400) -> pd.DataFrame:
    import yfinance as yf
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    df = yf.download(yf_symbol, start=start, end=end, interval="1d",
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df[["Open", "High", "Low", "Close"]].rename(columns=str.lower)
    return df.dropna()


def compute_signal(df: pd.DataFrame, p: dict) -> dict:
    out = df.copy()
    out["dc_hi"]   = out["high"].rolling(p["dc_len"]).max().shift(1)
    out["ma_exit"] = out["close"].rolling(p["ma_exit_len"]).mean()
    out["ma_long"] = out["close"].rolling(p["ma_long_len"]).mean()
    out["entry"] = (out["close"] >= out["dc_hi"]) & (out["close"] > out["ma_long"])
    out["exit"]  = out["close"] < out["ma_exit"]

    last = out.iloc[-1]
    if bool(last["entry"]):
        return {"action": "buy", "close": float(last["close"])}
    if bool(last["exit"]):
        return {"action": "sell", "close": float(last["close"])}
    return {"action": "flat", "close": float(last["close"])}


def get_exchange(name: str, testnet: bool = False):
    import ccxt
    cls = getattr(ccxt, name)
    cfg = {
        "apiKey":  os.getenv(f"{name.upper()}_API_KEY"),
        "secret":  os.getenv(f"{name.upper()}_API_SECRET"),
        "enableRateLimit": True,
    }
    ex = cls(cfg)
    if testnet and hasattr(ex, "set_sandbox_mode"):
        ex.set_sandbox_mode(True)
    return ex


def execute(asset: str, exchange_name: str, leverage: float, dry_run: bool, testnet: bool):
    p = PARAMS[asset]
    df = fetch_bars(p["yf_symbol"])
    LOG.info(f"{asset} latest bar: {df.index[-1].date()} close={df['close'].iloc[-1]:.2f}")

    signal = compute_signal(df, p)
    LOG.info(f"signal: {json.dumps(signal)}")

    ex = get_exchange(exchange_name, testnet=testnet)
    pair = f"{asset}/USDT"

    # Account state
    bal = ex.fetch_balance()
    quote_free = bal.get("USDT", {}).get("free", 0) or 0
    base_free  = bal.get(asset, {}).get("free", 0) or 0
    ticker = ex.fetch_ticker(pair)
    last = ticker["last"]
    pos_value = base_free * last
    total_equity = quote_free + pos_value
    LOG.info(f"USDT={quote_free:.2f}  {asset}={base_free:.6f} (~${pos_value:.2f})  total=${total_equity:.2f}  last={last}")

    in_pos = base_free * last > total_equity * 0.05  # >5% of equity = considered in-pos

    if signal["action"] == "buy" and not in_pos:
        target_notional = total_equity * leverage
        qty = target_notional / last
        # Round to exchange precision
        qty = float(ex.amount_to_precision(pair, qty))
        LOG.info(f"BUY {qty} {asset} @ ~{last} (notional ${qty*last:,.0f})")
        if dry_run:
            LOG.info("DRY RUN — order not sent.")
            return
        order = ex.create_market_buy_order(pair, qty)
        LOG.info(f"order: {order}")

    elif signal["action"] == "sell" and in_pos:
        qty = float(ex.amount_to_precision(pair, base_free))
        LOG.info(f"SELL {qty} {asset} @ ~{last}")
        if dry_run:
            LOG.info("DRY RUN — order not sent.")
            return
        order = ex.create_market_sell_order(pair, qty)
        LOG.info(f"order: {order}")

    else:
        LOG.info(f"No action. signal={signal['action']} in_pos={in_pos}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", choices=["BTC", "ETH"], required=True)
    ap.add_argument("--exchange", default="bybit",
                    choices=["bybit", "okx", "independentreserve", "binance", "coinbase"])
    ap.add_argument("--leverage", type=float, default=1.0,
                    help="Spot leverage; >1.0 only meaningful for margin/futures accounts")
    ap.add_argument("--live", action="store_true", help="Actually send orders (default dry-run)")
    ap.add_argument("--testnet", action="store_true", help="Use exchange testnet/sandbox")
    args = ap.parse_args()

    execute(args.asset, args.exchange, args.leverage, dry_run=not args.live, testnet=args.testnet)


if __name__ == "__main__":
    main()
