"""
Crypto-aware vectorized backtester for BTC/ETH.

Differences vs gold:
  - Crypto trades 24/7, but yfinance daily bars are still daily-close. Spread is wider.
  - Realistic spread: 0.05% of price (5 bps each side on Binance retail) ~ much wider in $ terms than gold.
  - Slippage: 0.05% of price.
  - Cash yield while flat: 4% APY (USDC on Binance Earn / T-bills via IBKR).
  - Funding rate not modelled (we assume spot, not perp).
  - One position at a time, long-only (matches "buy low / sell high" thesis).
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def load_crypto(sym: str) -> pd.DataFrame:
    """sym = 'BTC' or 'ETH'."""
    df = pd.read_csv(os.path.join(DATA_DIR, f"{sym}_daily.csv"),
                     header=[0, 1], index_col=0, parse_dates=True)
    df.columns = [c[0] for c in df.columns]
    df = df[["Open", "High", "Low", "Close", "Volume"]].rename(columns=str.lower)
    # Macro overlays for hybrid strategies
    for name, fname, col in [
        ("dxy", "DXY_daily.csv", "Close"),
        ("spx", "SPX_daily.csv", "Close"),
        ("vix", "VIX_daily.csv", "Close"),
        ("tnx", "TNX_daily.csv", "Close"),
    ]:
        m = pd.read_csv(os.path.join(DATA_DIR, fname),
                        header=[0, 1], index_col=0, parse_dates=True)
        m.columns = [c[0] for c in m.columns]
        df[name] = m[col].reindex(df.index).ffill()
    return df.dropna()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


# --------------------------------------------------------------------------
# Strategy variants
# --------------------------------------------------------------------------

def strat_v8_gold(df: pd.DataFrame) -> pd.DataFrame:
    """v8 GOLD logic dropped onto crypto, unchanged. Expectation: poor fit."""
    out = df.copy()
    out["ma_pull"] = out["close"].rolling(10).mean()
    out["ma_exit"] = out["close"].rolling(50).mean()
    out["ma_long"] = out["close"].rolling(200).mean()
    out["dxy_chg"] = out["dxy"].pct_change(60)
    out["tnx_chg"] = out["tnx"].diff(60)
    out["rsi"] = rsi(out["close"], 14)
    macro = (out["dxy_chg"] < 0) & (out["tnx_chg"] < 0) & (out["close"] > out["ma_long"])
    pull  = (out["rsi"] < 50) & (out["close"] <= out["ma_pull"] * 1.05)
    out["entry"] = macro & pull
    out["exit"]  = (out["close"] < out["ma_exit"]) | (out["rsi"] > 75)
    return out


def strat_c1_trend(df: pd.DataFrame) -> pd.DataFrame:
    """C1 - Long-term trend follower. Long when 50MA > 200MA AND close > 200MA. Exit on close < 100MA."""
    out = df.copy()
    out["ma50"]  = out["close"].rolling(50).mean()
    out["ma100"] = out["close"].rolling(100).mean()
    out["ma200"] = out["close"].rolling(200).mean()
    out["entry"] = (out["close"] > out["ma200"]) & (out["ma50"] > out["ma200"])
    out["exit"]  = out["close"] < out["ma100"]
    return out


def strat_c2_donchian(df: pd.DataFrame) -> pd.DataFrame:
    """C2 - Donchian 55/20 breakout. Classic turtle, with 200MA bull filter."""
    out = df.copy()
    out["dc_hi"] = out["high"].rolling(55).max().shift(1)
    out["dc_lo"] = out["low"].rolling(20).min().shift(1)
    out["ma200"] = out["close"].rolling(200).mean()
    out["entry"] = (out["close"] >= out["dc_hi"]) & (out["close"] > out["ma200"])
    out["exit"]  = out["close"] <= out["dc_lo"]
    return out


def strat_c3_mr(df: pd.DataFrame) -> pd.DataFrame:
    """C3 - Mean reversion in uptrend. Buy RSI<25 dips when above 200MA. Exit on RSI>55."""
    out = df.copy()
    out["ma200"] = out["close"].rolling(200).mean()
    out["rsi"]   = rsi(out["close"], 14)
    out["entry"] = (out["rsi"] < 25) & (out["close"] > out["ma200"])
    out["exit"]  = out["rsi"] > 55
    return out


def strat_c4_dual_ma(df: pd.DataFrame) -> pd.DataFrame:
    """C4 - 50/200 golden-cross / death-cross. Pure regime."""
    out = df.copy()
    out["ma50"]  = out["close"].rolling(50).mean()
    out["ma200"] = out["close"].rolling(200).mean()
    out["entry"] = (out["ma50"] > out["ma200"]) & (out["ma50"].shift(1) <= out["ma200"].shift(1))
    out["exit"]  = (out["ma50"] < out["ma200"]) & (out["ma50"].shift(1) >= out["ma200"].shift(1))
    return out


def strat_c5_hybrid(df: pd.DataFrame) -> pd.DataFrame:
    """C5 - Hybrid: stay in trend regime + add on pullbacks.
    Regime: close > 200MA AND 50MA slope > 0.
    Add (entry signal) when in regime AND RSI < 45.
    Exit when close < 100MA OR RSI > 80 (parabolic blow-off)."""
    out = df.copy()
    out["ma20"]  = out["close"].rolling(20).mean()
    out["ma50"]  = out["close"].rolling(50).mean()
    out["ma100"] = out["close"].rolling(100).mean()
    out["ma200"] = out["close"].rolling(200).mean()
    out["ma50_slope"] = out["ma50"].diff(20) / out["ma50"].shift(20)
    out["rsi"]   = rsi(out["close"], 14)
    regime = (out["close"] > out["ma200"]) & (out["ma50_slope"] > 0)
    out["entry"] = regime & (out["rsi"] < 45)
    out["exit"]  = (out["close"] < out["ma100"]) | (out["rsi"] > 80)
    return out


def strat_c6_volwf(df: pd.DataFrame) -> pd.DataFrame:
    """C6 - Volatility-weighted breakout. Donchian-20 entries with 200MA filter,
    exit when close drops 1*ATR below entry (chandelier-style trail) OR breaks 50MA."""
    out = df.copy()
    out["dc_hi20"] = out["high"].rolling(20).max().shift(1)
    out["ma50"]   = out["close"].rolling(50).mean()
    out["ma200"]  = out["close"].rolling(200).mean()
    out["atr20"]  = atr(out, 20)
    out["entry"] = (out["close"] >= out["dc_hi20"]) & (out["close"] > out["ma200"])
    out["exit"]  = out["close"] < out["ma50"]
    return out


# --------------------------------------------------------------------------
# Backtest runner with crypto-realistic costs
# --------------------------------------------------------------------------

def run_crypto(sig: pd.DataFrame, initial_equity: float = 100_000.0,
               leverage: float = 1.0, spread_pct: float = 0.0005,
               slip_pct: float = 0.0005, cash_apy: float = 0.04) -> dict:
    """Long-only single-position. Spread + slippage as % of price (5bps each)."""
    daily_cash = (1 + cash_apy) ** (1 / 365) - 1   # 365 days/yr for crypto

    equity = initial_equity
    in_pos = False
    entry_price = qty = 0.0
    entry_idx = -1
    bars_held = 0
    eq_curve, trades = [], []

    o, h, l, c = (sig[k].values for k in ["open","high","low","close"])
    e_sig = sig["entry"].values
    x_sig = sig["exit"].values
    dates = sig.index

    for i in range(len(sig)):
        if i == 0:
            eq_curve.append(equity); continue

        if not in_pos:
            equity *= (1 + daily_cash)

        if in_pos:
            bars_held += 1
            sig_exit = x_sig[i - 1]
            if sig_exit:
                exit_price = o[i] * (1 - slip_pct - spread_pct / 2)
                pnl = (exit_price - entry_price) * qty
                equity += pnl
                trades.append({"entry_date": dates[entry_idx], "exit_date": dates[i],
                               "entry": entry_price, "exit": exit_price, "qty": qty,
                               "pnl": pnl, "ret_pct": pnl / (entry_price * qty),
                               "bars": bars_held})
                in_pos = False; qty = 0; bars_held = 0

        if not in_pos and e_sig[i - 1]:
            fill = o[i] * (1 + slip_pct + spread_pct / 2)
            qty = (equity * leverage) / fill
            entry_price = fill
            entry_idx = i
            in_pos = True
            bars_held = 0

        eq_curve.append(equity + (c[i] - entry_price) * qty if in_pos else equity)

    eq = pd.Series(eq_curve, index=sig.index)
    tr = pd.DataFrame(trades)
    daily = eq.pct_change().fillna(0)
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    metrics = {
        "total_return": eq.iloc[-1] / initial_equity - 1,
        "cagr":         (eq.iloc[-1] / initial_equity) ** (1 / max(years, 1e-9)) - 1,
        "sharpe":       daily.mean() / daily.std() * np.sqrt(365) if daily.std() > 0 else 0,
        "max_dd":       (eq / eq.cummax() - 1).min(),
        "n_trades":     len(tr),
        "win_rate":     (tr["pnl"] > 0).mean() if len(tr) else 0,
        "profit_factor":(tr.loc[tr["pnl"]>0,"pnl"].sum() /
                        max(-tr.loc[tr["pnl"]<=0,"pnl"].sum(), 1e-9)) if len(tr) else 0,
        "avg_hold":     tr["bars"].mean() if len(tr) else 0,
        "exposure":     (tr["bars"].sum() / len(sig)) if len(tr) else 0,
    }
    metrics["calmar"] = metrics["cagr"] / abs(metrics["max_dd"]) if metrics["max_dd"] else 0
    return {"equity": eq, "trades": tr, "metrics": metrics}


def buy_hold_crypto(df: pd.DataFrame, initial_equity: float = 100_000.0) -> dict:
    qty = initial_equity / df["open"].iloc[0]
    eq = df["close"] * qty
    eq.iloc[0] = initial_equity
    daily = eq.pct_change().fillna(0)
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    return {"equity": eq, "metrics": {
        "total_return": eq.iloc[-1] / initial_equity - 1,
        "cagr": (eq.iloc[-1] / initial_equity) ** (1 / max(years, 1e-9)) - 1,
        "sharpe": daily.mean() / daily.std() * np.sqrt(365) if daily.std() > 0 else 0,
        "max_dd": (eq / eq.cummax() - 1).min(),
    }}
