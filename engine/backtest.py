"""
Vectorized daily backtester for XAU/USD strategies.

Conservative defaults:
- Spread: 0.30 USD per ounce (typical retail gold CFD)
- Commission: 0.0 (built into spread)
- Slippage: 0.05 USD per ounce on entry and exit
- Risk per trade: 1% of equity, sized off ATR-based stop distance
- One position at a time, long-only (we are "buying low, selling high")
- Signals computed on bar t, executed at open of bar t+1 (no look-ahead)
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def load_data() -> pd.DataFrame:
    def _read(name: str, col: str = "Close") -> pd.Series:
        df = pd.read_csv(os.path.join(DATA_DIR, name), header=[0, 1], index_col=0, parse_dates=True)
        df.columns = [c[0] for c in df.columns]
        return df[col]

    gold = pd.read_csv(os.path.join(DATA_DIR, "XAUUSD_daily.csv"), header=[0, 1], index_col=0, parse_dates=True)
    gold.columns = [c[0] for c in gold.columns]
    gold = gold[["Open", "High", "Low", "Close"]].rename(columns=str.lower)

    dxy = _read("DXY_daily.csv")
    tnx = _read("TNX_daily.csv")
    vix = _read("VIX_daily.csv")
    spx = _read("SPX_daily.csv")

    df = gold.copy()
    df["dxy"] = dxy.reindex(df.index).ffill()
    df["tnx"] = tnx.reindex(df.index).ffill()
    df["vix"] = vix.reindex(df.index).ffill()
    df["spx"] = spx.reindex(df.index).ffill()
    df = df.dropna()
    return df


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    delta = s.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / down.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def zscore(s: pd.Series, n: int) -> pd.Series:
    return (s - s.rolling(n).mean()) / s.rolling(n).std()


# ---------------------------------------------------------------------------
# Strategy signal generators. Each returns a boolean series for entries
# and a dict of stop / take params per bar.
# ---------------------------------------------------------------------------

def strat_v1_rsi(df: pd.DataFrame) -> pd.DataFrame:
    """v1 - Pure RSI mean reversion. Buy when RSI(14) < 30, exit when RSI > 55."""
    out = df.copy()
    out["rsi"] = rsi(out["close"], 14)
    out["entry"] = out["rsi"] < 30
    out["exit"] = out["rsi"] > 55
    out["atr"] = atr(out, 14)
    out["stop_mult"] = 2.0
    return out


def strat_v2_bb(df: pd.DataFrame) -> pd.DataFrame:
    """v2 - Bollinger Band reversion with 200MA trend filter.
    Buy when close < lower BB AND close > 200MA (only buy dips in uptrend)."""
    out = df.copy()
    n = 20
    ma = out["close"].rolling(n).mean()
    sd = out["close"].rolling(n).std()
    out["bb_low"] = ma - 2 * sd
    out["bb_mid"] = ma
    out["ma200"] = out["close"].rolling(200).mean()
    out["entry"] = (out["close"] < out["bb_low"]) & (out["close"] > out["ma200"])
    out["exit"] = out["close"] > out["bb_mid"]
    out["atr"] = atr(out, 14)
    out["stop_mult"] = 2.5
    return out


def strat_v3_macro(df: pd.DataFrame) -> pd.DataFrame:
    """v3 - Macro-filtered mean reversion.
    Gold loves: weakening dollar, falling real yields, elevated fear.
    Buy when:
      - close < 20MA - 1*sigma (oversold)
      - DXY rolling 20d return < 0 (dollar weakening)
      - VIX > 18 (some fear in market)
    Exit when close > 20MA + 0.5*sigma OR holding > 20 bars.
    """
    out = df.copy()
    n = 20
    ma = out["close"].rolling(n).mean()
    sd = out["close"].rolling(n).std()
    out["ma20"] = ma
    out["zclose"] = (out["close"] - ma) / sd
    out["dxy_chg"] = out["dxy"].pct_change(20)
    out["entry"] = (out["zclose"] < -1.0) & (out["dxy_chg"] < 0) & (out["vix"] > 18)
    out["exit"] = out["zclose"] > 0.5
    out["atr"] = atr(out, 14)
    out["stop_mult"] = 2.0
    out["max_hold"] = 20
    return out


def strat_v4_adaptive(df: pd.DataFrame) -> pd.DataFrame:
    """v4 - Adaptive regime strategy.
    - Trend regime (ADX-style: 50MA slope strong) -> momentum: buy 50MA cross + pullback to 20MA.
    - Range regime -> RSI mean reversion.
    """
    out = df.copy()
    out["ma20"] = out["close"].rolling(20).mean()
    out["ma50"] = out["close"].rolling(50).mean()
    out["ma200"] = out["close"].rolling(200).mean()
    out["ma50_slope"] = out["ma50"].diff(20) / out["ma50"].shift(20)
    out["rsi"] = rsi(out["close"], 14)
    out["atr"] = atr(out, 14)

    trend_regime = (out["ma50_slope"] > 0.02) & (out["close"] > out["ma200"])
    range_regime = ~trend_regime

    trend_entry = trend_regime & (out["close"] <= out["ma20"]) & (out["close"].shift(1) > out["ma20"].shift(1))
    range_entry = range_regime & (out["rsi"] < 30)

    out["entry"] = trend_entry | range_entry
    out["exit"] = (out["rsi"] > 60) | (out["close"] < out["ma50"])
    out["stop_mult"] = 2.0
    return out


def strat_v5_composite(df: pd.DataFrame) -> pd.DataFrame:
    """v5 - Composite macro + technical with quality scoring.

    Buy when COMPOSITE SCORE >= threshold. Score components (each 0/1):
      A) Technical oversold:  zscore(close,20) < -1.0
      B) Long-term uptrend:   close > MA200  (only buy dips in secular bull)
      C) Dollar weakening:    DXY 20d return < 0
      D) Real-yield proxy:    TNX 20d change < 0  (yields falling -> gold up)
      E) Fear/risk-off:       VIX > 16
      F) Not overbought:      RSI(14) < 50

    Need 4 of 6. Exit on: zscore > 0.75 OR close < entry - 2*ATR (stop) OR hold > 30 bars.
    """
    out = df.copy()
    n = 20
    ma = out["close"].rolling(n).mean()
    sd = out["close"].rolling(n).std()
    out["zclose"] = (out["close"] - ma) / sd
    out["ma200"] = out["close"].rolling(200).mean()
    out["dxy_chg20"] = out["dxy"].pct_change(20)
    out["tnx_chg20"] = out["tnx"].diff(20)
    out["rsi"] = rsi(out["close"], 14)
    out["atr"] = atr(out, 14)

    A = out["zclose"] < -1.0
    B = out["close"] > out["ma200"]
    C = out["dxy_chg20"] < 0
    D = out["tnx_chg20"] < 0
    E = out["vix"] > 16
    F = out["rsi"] < 50

    score = A.astype(int) + B.astype(int) + C.astype(int) + D.astype(int) + E.astype(int) + F.astype(int)
    out["score"] = score
    out["entry"] = score >= 4
    out["exit"] = (out["zclose"] > 0.75) | (out["rsi"] > 65)
    out["stop_mult"] = 2.5
    out["max_hold"] = 30
    return out


def strat_v6_trend(df: pd.DataFrame) -> pd.DataFrame:
    """v6 - Long-term trend following. Long when close > 200MA AND 50MA > 200MA.
    Exit when close < 100MA. Always full-size (no fractional risk sizing)."""
    out = df.copy()
    out["ma50"] = out["close"].rolling(50).mean()
    out["ma100"] = out["close"].rolling(100).mean()
    out["ma200"] = out["close"].rolling(200).mean()
    out["entry"] = (out["close"] > out["ma200"]) & (out["ma50"] > out["ma200"])
    out["exit"] = out["close"] < out["ma100"]
    out["atr"] = atr(out, 14)
    out["stop_mult"] = 4.0
    return out


def strat_v7_donchian(df: pd.DataFrame) -> pd.DataFrame:
    """v7 - Donchian breakout (turtle-style adapted for gold).
    Buy on 55-day high. Exit on 20-day low.
    Macro confirmation: long-term uptrend filter (close > 200MA)."""
    out = df.copy()
    out["dc_hi"] = out["high"].rolling(55).max().shift(1)
    out["dc_lo"] = out["low"].rolling(20).min().shift(1)
    out["ma200"] = out["close"].rolling(200).mean()
    out["entry"] = (out["close"] >= out["dc_hi"]) & (out["close"] > out["ma200"])
    out["exit"] = out["close"] <= out["dc_lo"]
    out["atr"] = atr(out, 20)
    out["stop_mult"] = 3.0
    return out


def strat_v8_macro_trend(df: pd.DataFrame) -> pd.DataFrame:
    """v8 - Macro-confirmed trend with pullback entries (THE HYBRID).

    The thesis: gold trends up across cycles when:
      - real yields are falling (TNX falling)
      - dollar weakening (DXY falling 60-day)
      - secular bull intact (close > 200MA)
    Buy pullbacks (close <= 20MA) inside that regime.
    Exit when close < 100MA (regime change).
    """
    out = df.copy()
    out["ma20"] = out["close"].rolling(20).mean()
    out["ma100"] = out["close"].rolling(100).mean()
    out["ma200"] = out["close"].rolling(200).mean()
    out["dxy_chg60"] = out["dxy"].pct_change(60)
    out["tnx_chg60"] = out["tnx"].diff(60)
    out["rsi"] = rsi(out["close"], 14)
    out["atr"] = atr(out, 14)

    macro_bull = (out["dxy_chg60"] < 0.02) & (out["tnx_chg60"] < 0.5) & (out["close"] > out["ma200"])
    pullback = (out["rsi"] < 55) & (out["close"] <= out["ma20"] * 1.02)

    out["entry"] = macro_bull & pullback
    out["exit"] = (out["close"] < out["ma100"]) | (out["rsi"] > 75)
    out["stop_mult"] = 3.0
    return out


# ---------------------------------------------------------------------------
# Event-driven runner over the precomputed signal frame.
# ---------------------------------------------------------------------------

def run_backtest(
    sig: pd.DataFrame,
    initial_equity: float = 100_000.0,
    risk_per_trade: float = 0.02,
    spread: float = 0.30,
    slippage: float = 0.05,
    max_hold: int | None = None,
    leverage_cap: float = 1.0,  # 1.0 = unlevered, max notional = equity
    full_size: bool = False,    # if True, deploy leverage_cap * equity each trade (ignore ATR sizing)
) -> dict:
    """Long-only single-position runner. Executes next-bar-open."""
    sig = sig.copy()
    if "max_hold" in sig.columns:
        max_hold_series = sig["max_hold"]
    else:
        max_hold_series = None

    equity = initial_equity
    in_pos = False
    entry_price = 0.0
    entry_idx = -1
    qty = 0.0
    stop = 0.0
    bars_held = 0

    equity_curve = []
    trades = []

    o = sig["open"].values
    h = sig["high"].values
    l = sig["low"].values
    c = sig["close"].values
    entry_sig = sig["entry"].values
    exit_sig = sig["exit"].values
    atr_v = sig["atr"].values
    stop_mult = sig["stop_mult"].values

    dates = sig.index

    for i in range(len(sig)):
        if i == 0:
            equity_curve.append(equity)
            continue

        # If in position, check exit on this bar
        if in_pos:
            bars_held += 1
            # Stop hit intraday
            stopped = l[i] <= stop
            # Time stop
            time_stop = max_hold_series is not None and not pd.isna(max_hold_series.iloc[i]) and bars_held >= int(max_hold_series.iloc[i])
            time_stop = time_stop or (max_hold is not None and bars_held >= max_hold)
            # Signal exit (use today's signal computed on yesterday's data)
            sig_exit = exit_sig[i - 1]

            exit_price = None
            reason = None
            if stopped:
                exit_price = stop - slippage  # gap-down assumption: fill at stop minus slippage
                reason = "stop"
            elif sig_exit or time_stop:
                exit_price = o[i] - slippage - spread / 2
                reason = "signal" if sig_exit else "time"

            if exit_price is not None:
                pnl = (exit_price - entry_price) * qty
                equity += pnl
                trades.append({
                    "entry_date": dates[entry_idx],
                    "exit_date": dates[i],
                    "entry": entry_price,
                    "exit": exit_price,
                    "qty": qty,
                    "pnl": pnl,
                    "ret_pct": pnl / (entry_price * qty),
                    "bars": bars_held,
                    "reason": reason,
                })
                in_pos = False
                qty = 0.0
                bars_held = 0

        # Entry: only if flat AND yesterday's bar fired entry (no look-ahead)
        if not in_pos and entry_sig[i - 1]:
            fill = o[i] + slippage + spread / 2
            risk_per_share = stop_mult[i - 1] * atr_v[i - 1]
            if risk_per_share <= 0 or np.isnan(risk_per_share):
                equity_curve.append(equity)
                continue
            if full_size:
                qty = (equity * leverage_cap) / fill
            else:
                dollar_risk = equity * risk_per_trade
                qty = dollar_risk / risk_per_share
                max_qty = (equity * leverage_cap) / fill
                qty = min(qty, max_qty)
            entry_price = fill
            stop = fill - risk_per_share
            entry_idx = i
            in_pos = True
            bars_held = 0

        equity_curve.append(equity + (c[i] - entry_price) * qty if in_pos else equity)

    eq = pd.Series(equity_curve, index=sig.index)
    trades_df = pd.DataFrame(trades)

    # Metrics
    total_return = eq.iloc[-1] / initial_equity - 1
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / initial_equity) ** (1 / max(years, 1e-9)) - 1
    daily_ret = eq.pct_change().fillna(0)
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    rolling_max = eq.cummax()
    dd = (eq / rolling_max - 1)
    max_dd = dd.min()
    calmar = cagr / abs(max_dd) if max_dd != 0 else 0
    n_trades = len(trades_df)
    if n_trades > 0:
        win_rate = (trades_df["pnl"] > 0).mean()
        avg_win = trades_df.loc[trades_df["pnl"] > 0, "pnl"].mean()
        avg_loss = trades_df.loc[trades_df["pnl"] <= 0, "pnl"].mean()
        gross_profit = trades_df.loc[trades_df["pnl"] > 0, "pnl"].sum()
        gross_loss = -trades_df.loc[trades_df["pnl"] <= 0, "pnl"].sum()
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        avg_hold = trades_df["bars"].mean()
    else:
        win_rate = avg_win = avg_loss = pf = avg_hold = 0

    return {
        "equity": eq,
        "trades": trades_df,
        "metrics": {
            "total_return": total_return,
            "cagr": cagr,
            "sharpe": sharpe,
            "max_dd": max_dd,
            "calmar": calmar,
            "n_trades": n_trades,
            "win_rate": win_rate,
            "profit_factor": pf,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "avg_hold_bars": avg_hold,
        },
    }


def buy_hold(df: pd.DataFrame, initial_equity: float = 100_000.0) -> dict:
    """Benchmark: buy first bar, hold."""
    qty = initial_equity / df["open"].iloc[0]
    eq = df["close"] * qty
    eq.iloc[0] = initial_equity
    total_return = eq.iloc[-1] / initial_equity - 1
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / initial_equity) ** (1 / max(years, 1e-9)) - 1
    daily_ret = eq.pct_change().fillna(0)
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    dd = (eq / eq.cummax() - 1)
    return {"equity": eq, "metrics": {"total_return": total_return, "cagr": cagr, "sharpe": sharpe, "max_dd": dd.min()}}
