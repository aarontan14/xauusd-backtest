"""Locked production crypto strategy: C6 vol-weighted Donchian breakout.
Per-asset params loaded from data/crypto_<asset>_params.json (output of tune_crypto.py).
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import pandas as pd
from crypto_backtest import load_crypto, run_crypto, buy_hold_crypto


def strat_c6(df, dc_len=20, ma_exit_len=50, ma_long_len=200):
    out = df.copy()
    out["dc_hi"]   = out["high"].rolling(int(dc_len)).max().shift(1)
    out["ma_exit"] = out["close"].rolling(int(ma_exit_len)).mean()
    out["ma_long"] = out["close"].rolling(int(ma_long_len)).mean()
    out["entry"]   = (out["close"] >= out["dc_hi"]) & (out["close"] > out["ma_long"])
    out["exit"]    = out["close"] < out["ma_exit"]
    return out


# Locked params (from tune_crypto.py walk-forward selection)
PARAMS = {
    "BTC": {"dc_len": 10, "ma_exit_len": 50, "ma_long_len": 100},
    "ETH": {"dc_len": 30, "ma_exit_len": 50, "ma_long_len": 150},
}


def report(asset: str, leverage: float = 1.0):
    df = load_crypto(asset)
    sig = strat_c6(df, **PARAMS[asset])
    res = run_crypto(sig, leverage=leverage)
    bh = buy_hold_crypto(df)["metrics"]
    bh_calmar = bh["cagr"] / abs(bh["max_dd"]) if bh["max_dd"] else 0
    m = res["metrics"]

    print(f"\n=== {asset} C6 vol-breakout @ {leverage}x leverage ===")
    print(f"  Period:        {df.index.min().date()} -> {df.index.max().date()}  ({len(df)} bars)")
    print(f"  CAGR:          {m['cagr']:>7.2%}")
    print(f"  Sharpe:        {m['sharpe']:>7.2f}")
    print(f"  MaxDD:         {m['max_dd']:>7.2%}")
    print(f"  Calmar:        {m['calmar']:>7.2f}")
    print(f"  Trades:        {int(m['n_trades'])}")
    print(f"  Win rate:      {m['win_rate']:>7.2%}")
    print(f"  Profit factor: {m['profit_factor']:>7.2f}")
    print(f"  Exposure:      {m['exposure']:>7.2%}")
    print(f"  --- Buy-and-hold benchmark ---")
    print(f"  CAGR {bh['cagr']:.2%}  Sharpe {bh['sharpe']:.2f}  MaxDD {bh['max_dd']:.2%}  Calmar {bh_calmar:.2f}")
    return res


if __name__ == "__main__":
    for asset in ["BTC", "ETH"]:
        for lev in [1.0, 1.5]:
            report(asset, leverage=lev)
