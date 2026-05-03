"""Grid-search the C6 vol-weighted breakout on BTC and ETH with walk-forward."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import pandas as pd, numpy as np
from itertools import product
from crypto_backtest import load_crypto, run_crypto, atr


def strat_c6(df, dc_len=20, ma_exit_len=50, ma_long_len=200):
    out = df.copy()
    dc_len = int(dc_len); ma_exit_len = int(ma_exit_len); ma_long_len = int(ma_long_len)
    out["dc_hi"]   = out["high"].rolling(dc_len).max().shift(1)
    out["ma_exit"] = out["close"].rolling(ma_exit_len).mean()
    out["ma_long"] = out["close"].rolling(ma_long_len).mean()
    out["entry"]   = (out["close"] >= out["dc_hi"]) & (out["close"] > out["ma_long"])
    out["exit"]    = out["close"] < out["ma_exit"]
    return out


grid = {
    "dc_len":      [10, 20, 30, 40, 55],
    "ma_exit_len": [20, 50, 100],
    "ma_long_len": [100, 150, 200],
}
keys = list(grid.keys())
combos = list(product(*[grid[k] for k in keys]))
print(f"Grid size: {len(combos)}\n")


def score_combo(df_train, df_oos, params):
    sig_train = strat_c6(df_train, **params)
    res_train = run_crypto(sig_train, leverage=1.0)
    sig_oos = strat_c6(df_oos, **params)
    res_oos = run_crypto(sig_oos, leverage=1.0)
    m_train, m_oos = res_train["metrics"], res_oos["metrics"]
    return m_train, m_oos


for asset, split_date in [("BTC", "2022-12-31"), ("ETH", "2022-12-31")]:
    df = load_crypto(asset)
    train = df.loc[:split_date]
    test  = df.loc[split_date:]
    print(f"\n{'='*70}\n{asset}: train {train.index.min().date()}->{train.index.max().date()} ({len(train)} bars), "
          f"test {test.index.min().date()}->{test.index.max().date()} ({len(test)} bars)\n{'='*70}")

    rows = []
    for vals in combos:
        p = dict(zip(keys, vals))
        try:
            m_tr, m_oos = score_combo(train, df.loc["2017-01-01":], p)  # full df for OOS warm-up
        except Exception as e:
            continue
        # OOS slice metrics only
        sig_oos = strat_c6(df, **p)
        res_oos_slice = run_crypto(sig_oos.loc[split_date:], leverage=1.0)
        m_oos_only = res_oos_slice["metrics"]
        rows.append({**p,
                     "tr_cagr": m_tr["cagr"], "tr_sharpe": m_tr["sharpe"], "tr_dd": m_tr["max_dd"],
                     "tr_calmar": m_tr["calmar"], "tr_n": m_tr["n_trades"],
                     "oos_cagr": m_oos_only["cagr"], "oos_sharpe": m_oos_only["sharpe"], "oos_dd": m_oos_only["max_dd"],
                     "oos_calmar": m_oos_only["calmar"], "oos_n": m_oos_only["n_trades"]})

    rdf = pd.DataFrame(rows)
    rdf["robust"] = rdf[["tr_calmar","oos_calmar"]].min(axis=1)
    rdf = rdf[(rdf["tr_n"] >= 5) & (rdf["oos_n"] >= 2)].sort_values("robust", ascending=False)
    print(f"\nTop 8 by min(tr_calmar, oos_calmar):")
    print(rdf.head(8).to_string(index=False))

    best = rdf.iloc[0]
    best_params = {k: int(best[k]) for k in keys}
    print(f"\nBest params for {asset}: {best_params}")

    # Final full-sample run
    sig = strat_c6(df, **best_params)
    res = run_crypto(sig, leverage=1.0)
    print(f"\nFull-sample result @ 1.0x leverage:")
    for k, v in res["metrics"].items():
        if isinstance(v, (int, float)):
            print(f"  {k:14s} = {v:.4f}")

    # Save
    with open(os.path.join(os.path.dirname(__file__), "..", "data", f"crypto_{asset.lower()}_params.json"), "w") as f:
        json.dump(best_params, f, indent=2)
    print(f"  saved -> data/crypto_{asset.lower()}_params.json")
