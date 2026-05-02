"""Run all strategy variants and print a comparison table."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import pandas as pd
from backtest import (
    load_data, run_backtest, buy_hold,
    strat_v1_rsi, strat_v2_bb, strat_v3_macro, strat_v4_adaptive, strat_v5_composite,
    strat_v6_trend, strat_v7_donchian, strat_v8_macro_trend,
)

pd.set_option("display.float_format", lambda x: f"{x:.4f}")
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)

df = load_data()
print(f"Data: {df.index.min().date()} -> {df.index.max().date()}  ({len(df)} bars)")

# Configurations: (name, signal_fn, run_kwargs)
variants = [
    ("v1_RSI",          strat_v1_rsi,       dict(risk_per_trade=0.02, leverage_cap=1.0)),
    ("v2_BB",           strat_v2_bb,        dict(risk_per_trade=0.02, leverage_cap=1.0)),
    ("v3_Macro",        strat_v3_macro,     dict(risk_per_trade=0.02, leverage_cap=1.0)),
    ("v4_Adaptive",     strat_v4_adaptive,  dict(risk_per_trade=0.02, leverage_cap=1.0)),
    ("v5_Composite",    strat_v5_composite, dict(risk_per_trade=0.02, leverage_cap=1.0)),
    ("v6_Trend",        strat_v6_trend,     dict(full_size=True, leverage_cap=1.0)),
    ("v7_Donchian",     strat_v7_donchian,  dict(full_size=True, leverage_cap=1.0)),
    ("v8_MacroTrend",   strat_v8_macro_trend, dict(full_size=True, leverage_cap=1.0)),
]

rows = []
results = {}
for name, fn, kw in variants:
    sig = fn(df)
    res = run_backtest(sig, **kw)
    results[name] = res
    m = res["metrics"]
    rows.append({"strategy": name, **m})

bh = buy_hold(df)
m = bh["metrics"]
rows.append({"strategy": "buy_hold",
             "total_return": m["total_return"], "cagr": m["cagr"], "sharpe": m["sharpe"],
             "max_dd": m["max_dd"], "calmar": m["cagr"]/abs(m["max_dd"]) if m["max_dd"] else 0,
             "n_trades": 1, "win_rate": 1.0, "profit_factor": float("inf"),
             "avg_win": 0, "avg_loss": 0, "avg_hold_bars": len(df)})

cmp = pd.DataFrame(rows).set_index("strategy")
cols = ["total_return","cagr","sharpe","max_dd","calmar","n_trades","win_rate","profit_factor","avg_hold_bars"]
print("\n=== STRATEGY COMPARISON (full sample 2010-2026) ===")
print(cmp[cols])

# Save equity curves
out_dir = os.path.join(os.path.dirname(__file__), "..", "data")
eq_df = pd.DataFrame({k: v["equity"] for k, v in results.items()})
eq_df["buy_hold"] = bh["equity"]
eq_df.to_csv(os.path.join(out_dir, "equity_curves.csv"))
print("\nEquity curves saved to data/equity_curves.csv")
