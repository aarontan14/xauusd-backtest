"""
Live trading bot v2 — BTC/ETH C6 vol-breakout with vol-targeted sizing
+ optional hard ATR stop. Executes on a CCXT-supported exchange.

Improvements over live_crypto_bot.py:
  - Position size = min(vol_target / realized_vol, leverage_cap) * equity / price
    Reduces size when realized vol is high (drawdown defense).
  - Optional hard ATR stop submitted as a separate stop-loss order on the exchange.
  - Per-asset locked params loaded from PARAMS dict (BTC + ETH).

REQUIREMENTS:
  pip install ccxt yfinance pandas numpy

USAGE:
  Paper test on Bybit testnet:
    python live_crypto_bot_v2.py --asset BTC --exchange bybit --testnet --live

  Live, spot account (will cap leverage at 1.0x automatically):
    export BYBIT_API_KEY=...
    export BYBIT_API_SECRET=...
    python live_crypto_bot_v2.py --asset BTC --exchange bybit --mode spot --live

  Live, margin/perp account (allows leverage up to 3x for BTC, 2x for ETH):
    python live_crypto_bot_v2.py --asset BTC --exchange bybit --mode margin --live
"""
from __future__ import annotations
import argparse, json, logging, os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

LOG = logging.getLogger("crypto_bot_v2")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

PARAMS = {
    "BTC": {
        "yf_symbol": "BTC-USD",
        "signal":      {"dc_len": 10, "ma_exit_len": 50, "ma_long_len": 100},
        "risk_margin": {"vol_target": 0.025, "leverage_cap": 3.0, "hard_stop_atr": None},
        "risk_spot":   {"vol_target": 0.025, "leverage_cap": 1.0, "hard_stop_atr": None},
    },
    "ETH": {
        "yf_symbol": "ETH-USD",
        "signal":      {"dc_len": 30, "ma_exit_len": 50, "ma_long_len": 150},
        "risk_margin": {"vol_target": 0.030, "leverage_cap": 2.0, "hard_stop_atr": 3.0},
        "risk_spot":   {"vol_target": 0.030, "leverage_cap": 1.0, "hard_stop_atr": 3.0},
    },
}


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


def compute_state(df: pd.DataFrame, sig_p: dict, risk_p: dict) -> dict:
    """Return today's signal + sizing fraction + ATR for stop calc."""
    out = df.copy()
    out["dc_hi"]   = out["high"].rolling(sig_p["dc_len"]).max().shift(1)
    out["ma_exit"] = out["close"].rolling(sig_p["ma_exit_len"]).mean()
    out["ma_long"] = out["close"].rolling(sig_p["ma_long_len"]).mean()

    # ATR(14)
    h, l, c = out["high"], out["low"], out["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    out["atr"] = tr.ewm(alpha=1/14, adjust=False).mean()

    # 20-day realized daily-return vol
    out["rvol"] = out["close"].pct_change().rolling(20).std()

    out["entry"] = (out["close"] >= out["dc_hi"]) & (out["close"] > out["ma_long"])
    out["exit"]  = out["close"] < out["ma_exit"]

    last = out.iloc[-1]
    rvol = float(last["rvol"]) if not pd.isna(last["rvol"]) else 0.0
    cap  = risk_p["leverage_cap"]
    size_frac = min(risk_p["vol_target"] / rvol, cap) if rvol > 0 else cap

    state = {
        "close":     float(last["close"]),
        "atr":       float(last["atr"]) if not pd.isna(last["atr"]) else None,
        "rvol":      rvol,
        "size_frac": float(size_frac),
        "entry":     bool(last["entry"]),
        "exit":      bool(last["exit"]),
        "hard_stop_k": risk_p["hard_stop_atr"],
    }
    if state["entry"]:
        state["action"] = "buy"
    elif state["exit"]:
        state["action"] = "sell"
    else:
        state["action"] = "flat"
    return state


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


def execute(asset: str, exchange_name: str, mode: str, dry_run: bool, testnet: bool):
    cfg = PARAMS[asset]
    risk = cfg["risk_margin"] if mode == "margin" else cfg["risk_spot"]
    LOG.info(f"{asset} mode={mode} risk={risk}")

    df = fetch_bars(cfg["yf_symbol"])
    LOG.info(f"latest bar: {df.index[-1].date()}  close={df['close'].iloc[-1]:.2f}")

    state = compute_state(df, cfg["signal"], risk)
    LOG.info(f"state: {json.dumps({k: v for k, v in state.items() if k != 'hard_stop_k'})}")

    ex = get_exchange(exchange_name, testnet=testnet)
    pair = f"{asset}/USDT"

    bal = ex.fetch_balance()
    quote_free = bal.get("USDT", {}).get("free", 0) or 0
    base_free  = bal.get(asset, {}).get("free", 0) or 0
    ticker = ex.fetch_ticker(pair)
    last = ticker["last"]
    pos_value = base_free * last
    total_equity = quote_free + pos_value
    LOG.info(f"USDT={quote_free:.2f}  {asset}={base_free:.6f} (~${pos_value:.2f})  total=${total_equity:.2f}")

    in_pos = base_free * last > total_equity * 0.05

    if state["action"] == "buy" and not in_pos:
        target_notional = total_equity * state["size_frac"]
        qty = float(ex.amount_to_precision(pair, target_notional / last))
        LOG.info(f"BUY {qty} {asset} @ ~{last}  notional=${qty*last:,.0f}  size_frac={state['size_frac']:.3f}")
        if dry_run:
            LOG.info("DRY RUN — order not sent.")
            return
        order = ex.create_market_buy_order(pair, qty)
        LOG.info(f"entry order: {order.get('id')}")

        # Place hard stop if configured
        if state["hard_stop_k"] and state["atr"]:
            stop_px = last - state["hard_stop_k"] * state["atr"]
            stop_px = float(ex.price_to_precision(pair, stop_px))
            LOG.info(f"placing hard stop at {stop_px}")
            try:
                stop_order = ex.create_order(pair, "STOP_LOSS", "sell", qty, None,
                                             {"stopPrice": stop_px, "type": "STOP_LOSS"})
                LOG.info(f"stop order: {stop_order.get('id')}")
            except Exception as e:
                LOG.warning(f"could not place stop: {e}. Some exchanges require triggerPrice param. Configure manually if needed.")

    elif state["action"] == "sell" and in_pos:
        qty = float(ex.amount_to_precision(pair, base_free))
        LOG.info(f"SELL {qty} {asset} @ ~{last}")
        if dry_run:
            LOG.info("DRY RUN — order not sent.")
            return
        # Cancel any open stop orders first
        try:
            for o in ex.fetch_open_orders(pair):
                ex.cancel_order(o["id"], pair)
        except Exception as e:
            LOG.warning(f"could not cancel open orders: {e}")
        order = ex.create_market_sell_order(pair, qty)
        LOG.info(f"exit order: {order.get('id')}")

    else:
        LOG.info(f"No action. signal={state['action']} in_pos={in_pos}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", choices=["BTC", "ETH"], required=True)
    ap.add_argument("--exchange", default="bybit",
                    choices=["bybit", "okx", "independentreserve", "binance", "coinbase"])
    ap.add_argument("--mode", choices=["spot", "margin"], default="spot",
                    help="spot caps leverage at 1.0x; margin allows up to 3x BTC / 2x ETH")
    ap.add_argument("--live",    action="store_true")
    ap.add_argument("--testnet", action="store_true")
    args = ap.parse_args()

    execute(args.asset, args.exchange, args.mode, dry_run=not args.live, testnet=args.testnet)


if __name__ == "__main__":
    main()
