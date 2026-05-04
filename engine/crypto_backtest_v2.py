"""
Crypto backtest engine v2 — supports stops, trailing stops, and vol targeting.

Strategy is fixed (C6 vol-breakout); the engine is enriched with risk controls
that can be turned on/off independently:

  - hard_stop_atr   : float | None  -> stop at entry - k*ATR
  - trail_stop_atr  : float | None  -> chandelier trail at high_water - k*ATR
  - vol_target      : float | None  -> if set, size = (target_daily_vol / realized_daily_vol),
                                       capped at leverage. e.g. 0.02 = target 2%/day vol.
  - max_loss_dd     : float | None  -> intra-trade kill switch (% loss from entry)
"""
from __future__ import annotations
import os, numpy as np, pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def load_crypto(sym: str) -> pd.DataFrame:
    df = pd.read_csv(os.path.join(DATA_DIR, f"{sym}_daily.csv"),
                     header=[0, 1], index_col=0, parse_dates=True)
    df.columns = [c[0] for c in df.columns]
    df = df[["Open", "High", "Low", "Close", "Volume"]].rename(columns=str.lower)
    return df.dropna()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


def realized_vol(close: pd.Series, n: int = 20) -> pd.Series:
    return close.pct_change().rolling(n).std()


# --------------------------------------------------------------------------
# Strategy: C6 vol-breakout (entry/exit logic; risk controls handled by engine)
# --------------------------------------------------------------------------

def signals_c6(df, dc_len=20, ma_exit_len=50, ma_long_len=200) -> pd.DataFrame:
    out = df.copy()
    dc_len, ma_exit_len, ma_long_len = int(dc_len), int(ma_exit_len), int(ma_long_len)
    out["dc_hi"]   = out["high"].rolling(dc_len).max().shift(1)
    out["ma_exit"] = out["close"].rolling(ma_exit_len).mean()
    out["ma_long"] = out["close"].rolling(ma_long_len).mean()
    out["entry"]   = (out["close"] >= out["dc_hi"]) & (out["close"] > out["ma_long"])
    out["exit"]    = out["close"] < out["ma_exit"]
    out["atr"]     = atr(out, 14)
    out["rvol"]    = realized_vol(out["close"], 20)
    return out


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------

def run(sig: pd.DataFrame,
        initial_equity: float = 100_000.0,
        leverage_cap: float = 1.0,
        hard_stop_atr: float | None = None,
        trail_stop_atr: float | None = None,
        vol_target: float | None = None,
        max_loss_dd: float | None = None,
        spread_pct: float = 0.0005,
        slip_pct:   float = 0.0005,
        cash_apy:   float = 0.04) -> dict:
    """Long-only single-position runner with optional risk controls."""
    daily_cash = (1 + cash_apy) ** (1 / 365) - 1

    equity = initial_equity
    in_pos = False
    entry_price = qty = 0.0
    high_water = 0.0
    entry_idx = -1
    bars_held = 0
    eq_curve, trades = [], []

    o, h, l, c = (sig[k].values for k in ["open","high","low","close"])
    e_sig = sig["entry"].values
    x_sig = sig["exit"].values
    atr_v = sig["atr"].values
    rvol  = sig["rvol"].values
    dates = sig.index

    for i in range(len(sig)):
        if i == 0:
            eq_curve.append(equity); continue

        if not in_pos:
            equity *= (1 + daily_cash)

        if in_pos:
            bars_held += 1
            high_water = max(high_water, h[i])

            exit_price = None; reason = None

            # 1) hard stop (intra-day low <= stop)
            if hard_stop_atr is not None:
                hard_stop = entry_price - hard_stop_atr * atr_v[entry_idx]
                if l[i] <= hard_stop:
                    exit_price = hard_stop * (1 - slip_pct)
                    reason = "hard_stop"

            # 2) trailing stop (exit at trail level)
            if exit_price is None and trail_stop_atr is not None:
                trail = high_water - trail_stop_atr * atr_v[i - 1]
                if l[i] <= trail:
                    exit_price = trail * (1 - slip_pct)
                    reason = "trail_stop"

            # 3) max loss kill switch
            if exit_price is None and max_loss_dd is not None:
                pos_dd = (l[i] - entry_price) / entry_price
                if pos_dd <= max_loss_dd:
                    exit_price = entry_price * (1 + max_loss_dd) * (1 - slip_pct)
                    reason = "kill_switch"

            # 4) signal exit at next bar open
            if exit_price is None and x_sig[i - 1]:
                exit_price = o[i] * (1 - slip_pct - spread_pct / 2)
                reason = "signal"

            if exit_price is not None:
                pnl = (exit_price - entry_price) * qty
                equity += pnl
                trades.append({"entry_date": dates[entry_idx], "exit_date": dates[i],
                               "entry": entry_price, "exit": exit_price, "qty": qty,
                               "pnl": pnl, "ret_pct": pnl / (entry_price * qty),
                               "bars": bars_held, "reason": reason})
                in_pos = False; qty = 0; bars_held = 0; high_water = 0

        if not in_pos and e_sig[i - 1]:
            fill = o[i] * (1 + slip_pct + spread_pct / 2)
            # Position sizing
            if vol_target is not None and not np.isnan(rvol[i - 1]) and rvol[i - 1] > 0:
                size_mult = min(vol_target / rvol[i - 1], leverage_cap)
            else:
                size_mult = leverage_cap
            qty = (equity * size_mult) / fill
            entry_price = fill
            high_water = h[i]
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


def buy_hold(df: pd.DataFrame, initial_equity: float = 100_000.0) -> dict:
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
