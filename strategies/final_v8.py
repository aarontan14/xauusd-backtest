"""Final locked v8 strategy with leverage + cash yield realism."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import pandas as pd
import numpy as np
from backtest import load_data, atr, rsi, buy_hold


# Locked parameters (from tune_v8.py walk-forward selection)
PARAMS = {
    "ma_pull": 10,
    "ma_exit": 50,
    "ma_long": 200,
    "dxy_lb":  60,
    "dxy_thr": 0.0,
    "tnx_lb":  60,
    "tnx_thr": 0.0,
    "rsi_max": 50,
    "rsi_exit": 75,
    "pull_buf": 1.05,
    "stop_mult": 3.0,
}


def build_signals(df, p=PARAMS):
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
    return out


def run_realistic(df, leverage=1.0, initial_equity=100_000.0,
                  spread=0.30, slippage=0.05, cash_apy=0.04, stop_mult=3.0):
    """Backtest with leverage AND interest on cash when flat."""
    sig = build_signals(df)
    daily_cash_rate = (1 + cash_apy) ** (1 / 252) - 1

    equity = initial_equity
    in_pos = False
    entry_price = stop = qty = 0.0
    entry_idx = -1
    bars_held = 0
    eq_curve, trades = [], []

    o, h, l, c = (sig[k].values for k in ["open","high","low","close"])
    e_sig = sig["entry"].values
    x_sig = sig["exit"].values
    atr_v = sig["atr"].values
    dates = sig.index

    for i in range(len(sig)):
        if i == 0:
            eq_curve.append(equity); continue

        # accrue cash yield on flat capital (simplified: on full equity when flat,
        # on (equity - margin) when long; using leverage<=1 we model fully cash when flat)
        if not in_pos:
            equity *= (1 + daily_cash_rate)

        if in_pos:
            bars_held += 1
            stopped = l[i] <= stop
            sig_exit = x_sig[i - 1]

            exit_price = None; reason = None
            if stopped:
                exit_price = stop - slippage; reason = "stop"
            elif sig_exit:
                exit_price = o[i] - slippage - spread / 2; reason = "signal"

            if exit_price is not None:
                pnl = (exit_price - entry_price) * qty
                equity += pnl
                trades.append({"entry_date": dates[entry_idx], "exit_date": dates[i],
                               "entry": entry_price, "exit": exit_price, "qty": qty,
                               "pnl": pnl, "ret_pct": pnl / (entry_price * qty),
                               "bars": bars_held, "reason": reason})
                in_pos = False; qty = 0; bars_held = 0

        if not in_pos and e_sig[i - 1]:
            fill = o[i] + slippage + spread / 2
            risk_per = stop_mult * atr_v[i - 1]
            if risk_per <= 0 or np.isnan(risk_per):
                eq_curve.append(equity); continue
            qty = (equity * leverage) / fill
            entry_price = fill
            stop = fill - risk_per
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
        "cagr":         (eq.iloc[-1] / initial_equity) ** (1 / years) - 1,
        "sharpe":       daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else 0,
        "max_dd":       (eq / eq.cummax() - 1).min(),
        "n_trades":     len(tr),
        "win_rate":     (tr["pnl"] > 0).mean() if len(tr) else 0,
        "profit_factor":(tr.loc[tr["pnl"]>0,"pnl"].sum() /
                        max(-tr.loc[tr["pnl"]<=0,"pnl"].sum(), 1e-9)) if len(tr) else 0,
        "avg_hold":     tr["bars"].mean() if len(tr) else 0,
        "exposure":     (tr["bars"].sum() / len(sig)) if len(tr) else 0,
    }
    metrics["calmar"] = metrics["cagr"] / abs(metrics["max_dd"]) if metrics["max_dd"] else 0
    return eq, tr, metrics


if __name__ == "__main__":
    df = load_data()
    print(f"Data: {df.index.min().date()} -> {df.index.max().date()}\n")

    print("=== Locked v8 backtest, full sample, varying leverage (with 4% cash yield) ===")
    rows = []
    for lev in [1.0, 1.5, 2.0, 3.0]:
        eq, tr, m = run_realistic(df, leverage=lev)
        rows.append({"leverage": lev, **m})
    print(pd.DataFrame(rows).to_string(index=False))

    print("\n=== In-sample (train: 2010-2018) ===")
    df_train = df.loc[:"2018-12-31"]
    for lev in [1.0, 2.0]:
        eq, tr, m = run_realistic(df_train, leverage=lev)
        print(f"  Leverage {lev}x:  CAGR={m['cagr']:.2%}  Sharpe={m['sharpe']:.2f}  MaxDD={m['max_dd']:.2%}  Calmar={m['calmar']:.2f}  Trades={m['n_trades']}")

    print("\n=== Out-of-sample (test: 2019-2026) ===")
    df_oos = df.loc["2018-06-01":]
    for lev in [1.0, 2.0]:
        eq, tr, m = run_realistic(df_oos.loc["2019-01-01":], leverage=lev)
        print(f"  Leverage {lev}x:  CAGR={m['cagr']:.2%}  Sharpe={m['sharpe']:.2f}  MaxDD={m['max_dd']:.2%}  Calmar={m['calmar']:.2f}  Trades={m['n_trades']}")

    bh = buy_hold(df)
    m = bh["metrics"]
    print(f"\nBenchmark Buy-and-hold (full): CAGR={m['cagr']:.2%}  Sharpe={m['sharpe']:.2f}  MaxDD={m['max_dd']:.2%}  Calmar={m['cagr']/abs(m['max_dd']):.2f}")

    eq_full, tr_full, m_full = run_realistic(df, leverage=2.0)
    eq_full.to_csv(os.path.join(os.path.dirname(__file__), "..", "data", "v8_final_equity.csv"))
    tr_full.to_csv(os.path.join(os.path.dirname(__file__), "..", "data", "v8_final_trades.csv"), index=False)
    print(f"\nSaved final equity curve & trade log (leverage=2x).")
