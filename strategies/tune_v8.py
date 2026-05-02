"""Grid-search tune the v8 MacroTrend strategy and check OOS robustness."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import pandas as pd
import numpy as np
from itertools import product
from backtest import load_data, run_backtest, atr, rsi


def strat_v8_param(df, ma_pull=20, ma_exit=100, ma_long=200, dxy_lb=60, dxy_thr=0.02,
                   tnx_lb=60, tnx_thr=0.5, rsi_max=55, rsi_exit=75, pull_buf=1.02,
                   stop_mult=3.0):
    ma_pull = int(ma_pull); ma_exit = int(ma_exit); ma_long = int(ma_long)
    dxy_lb = int(dxy_lb); tnx_lb = int(tnx_lb)
    rsi_max = int(rsi_max); rsi_exit = int(rsi_exit)
    out = df.copy()
    out["ma_pull"] = out["close"].rolling(ma_pull).mean()
    out["ma_exit"] = out["close"].rolling(ma_exit).mean()
    out["ma_long"] = out["close"].rolling(ma_long).mean()
    out["dxy_chg"] = out["dxy"].pct_change(dxy_lb)
    out["tnx_chg"] = out["tnx"].diff(tnx_lb)
    out["rsi"] = rsi(out["close"], 14)
    out["atr"] = atr(out, 14)

    macro_bull = (out["dxy_chg"] < dxy_thr) & (out["tnx_chg"] < tnx_thr) & (out["close"] > out["ma_long"])
    pullback = (out["rsi"] < rsi_max) & (out["close"] <= out["ma_pull"] * pull_buf)
    out["entry"] = macro_bull & pullback
    out["exit"] = (out["close"] < out["ma_exit"]) | (out["rsi"] > rsi_exit)
    out["stop_mult"] = stop_mult
    return out


df = load_data()

# Train/Test split for walk-forward: 2010-2018 train, 2019-2026 test
train = df.loc[:"2018-12-31"]
test  = df.loc["2019-01-01":]

print(f"Train: {train.index.min().date()} -> {train.index.max().date()}  ({len(train)} bars)")
print(f"Test:  {test.index.min().date()} -> {test.index.max().date()}  ({len(test)} bars)")

# Grid search on training set
grid = {
    "ma_pull":  [10, 20, 30],
    "ma_exit":  [50, 100, 150],
    "rsi_max":  [50, 55, 65],
    "pull_buf": [1.00, 1.02, 1.05],
    "dxy_thr":  [0.0, 0.02, 0.05],
    "tnx_thr":  [0.0, 0.5, 1.0],
}

keys = list(grid.keys())
combos = list(product(*[grid[k] for k in keys]))
print(f"\nGrid search: {len(combos)} combos on TRAIN...")

results = []
for vals in combos:
    params = dict(zip(keys, vals))
    sig = strat_v8_param(train, **params)
    res = run_backtest(sig, full_size=True, leverage_cap=1.0)
    m = res["metrics"]
    results.append({**params, **m})

rdf = pd.DataFrame(results)
# Score: prefer high Calmar AND adequate trade count
rdf["score"] = rdf["calmar"] * np.where(rdf["n_trades"] >= 20, 1.0, 0.5)
rdf = rdf.sort_values("score", ascending=False)
print("\nTop 10 train results by Calmar (>=20 trades):")
print(rdf.head(10)[keys + ["cagr","sharpe","max_dd","calmar","n_trades","win_rate","profit_factor"]].to_string())

# Take top 5 and validate OOS
print("\n=== Out-of-sample (2019-2026) validation of top 5 ===")
top5 = rdf.head(5)
oos_rows = []
for _, row in top5.iterrows():
    params = {k: row[k] for k in keys}
    sig = strat_v8_param(df, **params)  # use full df so warmup is correct
    sig_oos = sig.loc["2019-01-01":]
    res = run_backtest(sig_oos, full_size=True, leverage_cap=1.0)
    oos_rows.append({**params, **res["metrics"]})
oos_df = pd.DataFrame(oos_rows)
print(oos_df[keys + ["cagr","sharpe","max_dd","calmar","n_trades","win_rate","profit_factor"]].to_string())

# Pick the most robust: best by min(train_score, oos_score)
print("\n=== Robust selection: best params that work train AND test ===")
combined = top5.reset_index(drop=True).copy()
combined["oos_calmar"] = oos_df["calmar"].values
combined["oos_cagr"]   = oos_df["cagr"].values
combined["oos_dd"]     = oos_df["max_dd"].values
combined["oos_sharpe"] = oos_df["sharpe"].values
combined["robust_score"] = combined[["calmar","oos_calmar"]].min(axis=1)
combined = combined.sort_values("robust_score", ascending=False)
print(combined[keys + ["calmar","oos_calmar","cagr","oos_cagr","oos_dd","oos_sharpe","robust_score"]].head(5).to_string())

best = combined.iloc[0]
print("\n=== BEST PARAMETERS ===")
best_params = {k: best[k] for k in keys}
for k, v in best_params.items():
    print(f"  {k:10s} = {v}")

# Final full-sample run with best params
print("\n=== Final full-sample backtest with best params ===")
sig = strat_v8_param(df, **best_params)
res = run_backtest(sig, full_size=True, leverage_cap=1.0)
for k, v in res["metrics"].items():
    print(f"  {k:18s} = {v:.4f}" if isinstance(v, (int, float)) else f"  {k}: {v}")

# Save best params for reuse
import json
with open(os.path.join(os.path.dirname(__file__), "..", "data", "best_params.json"), "w") as f:
    json.dump({k: float(v) for k, v in best_params.items()}, f, indent=2)
print("\nSaved best_params.json")
