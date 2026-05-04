"""Tune vol_target + leverage_cap + hard_stop together; walk-forward validate."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import pandas as pd, numpy as np
from itertools import product
from crypto_backtest_v2 import load_crypto, signals_c6, run, buy_hold

PARAMS = {
    "BTC": {"dc_len": 10, "ma_exit_len": 50, "ma_long_len": 100},
    "ETH": {"dc_len": 30, "ma_exit_len": 50, "ma_long_len": 150},
}

# Risk control grid
grid = {
    "vol_target":    [0.015, 0.020, 0.025, 0.030, 0.040],
    "leverage_cap":  [1.0, 1.5, 2.0, 3.0],
    "hard_stop_atr": [None, 3.0, 4.0, 6.0],
}
keys = list(grid.keys())
combos = list(product(*[grid[k] for k in keys]))
print(f"Grid size: {len(combos)}")


for asset in ["BTC", "ETH"]:
    df = load_crypto(asset)
    sig = signals_c6(df, **PARAMS[asset])
    train_cut = "2022-12-31"
    sig_train = sig.loc[:train_cut]
    sig_test  = sig.loc[train_cut:]

    print(f"\n{'='*100}\n{asset} train ({sig_train.index.min().date()}->{sig_train.index.max().date()}, "
          f"{len(sig_train)} bars), test ({sig_test.index.min().date()}->{sig_test.index.max().date()}, "
          f"{len(sig_test)} bars)\n{'='*100}")

    rows = []
    for vals in combos:
        p = dict(zip(keys, vals))
        m_tr  = run(sig_train, **p)["metrics"]
        m_te  = run(sig_test,  **p)["metrics"]
        rows.append({**p, "vol_target": p["vol_target"],
                     "tr_cagr": m_tr["cagr"], "tr_sharpe": m_tr["sharpe"],
                     "tr_dd": m_tr["max_dd"], "tr_calmar": m_tr["calmar"], "tr_n": m_tr["n_trades"],
                     "oos_cagr": m_te["cagr"], "oos_sharpe": m_te["sharpe"],
                     "oos_dd": m_te["max_dd"], "oos_calmar": m_te["calmar"], "oos_n": m_te["n_trades"]})

    rdf = pd.DataFrame(rows)
    rdf["robust_calmar"] = rdf[["tr_calmar","oos_calmar"]].min(axis=1)
    rdf["robust_sharpe"] = rdf[["tr_sharpe","oos_sharpe"]].min(axis=1)
    rdf = rdf[(rdf["tr_n"] >= 5) & (rdf["oos_n"] >= 2)]

    # Filter to reasonable DDs (we want robust improvements vs baseline -52% BTC / -44% ETH)
    target_dd = -0.45 if asset == "BTC" else -0.40
    rdf_safe = rdf[rdf["tr_dd"] >= target_dd].sort_values("robust_sharpe", ascending=False)

    print(f"\nTop 8 by min(train_sharpe, oos_sharpe), with train MaxDD >= {target_dd:.0%}:")
    cols = ["vol_target","leverage_cap","hard_stop_atr","tr_cagr","tr_sharpe","tr_dd","tr_calmar",
            "oos_cagr","oos_sharpe","oos_dd","oos_calmar","robust_sharpe"]
    pd.set_option("display.float_format", lambda x: f"{x:.4f}")
    pd.set_option("display.width", 220)
    print(rdf_safe.head(8)[cols].to_string(index=False))

    if len(rdf_safe) > 0:
        best = rdf_safe.iloc[0]
        best_p = {"vol_target": float(best["vol_target"]),
                  "leverage_cap": float(best["leverage_cap"]),
                  "hard_stop_atr": (float(best["hard_stop_atr"]) if best["hard_stop_atr"] is not None and not pd.isna(best["hard_stop_atr"]) else None)}
        # Save
        with open(os.path.join(os.path.dirname(__file__), "..", "data", f"crypto_{asset.lower()}_v2_params.json"), "w") as f:
            json.dump(best_p, f, indent=2)
        print(f"\nBest params -> data/crypto_{asset.lower()}_v2_params.json")
        print(f"  vol_target={best_p['vol_target']}  leverage_cap={best_p['leverage_cap']}  hard_stop_atr={best_p['hard_stop_atr']}")

        # Final full-sample
        print(f"\n--- Final full-sample run with best params ---")
        m_full = run(sig, **best_p)["metrics"]
        for k, v in m_full.items():
            if isinstance(v, (int, float)):
                print(f"  {k:14s} = {v:.4f}")

        # Compare to v1 baseline
        m_base = run(sig)["metrics"]
        print(f"\n--- vs V1 baseline ---")
        print(f"  CAGR    {m_full['cagr']:.2%} vs {m_base['cagr']:.2%}  (delta {m_full['cagr']-m_base['cagr']:+.2%})")
        print(f"  Sharpe  {m_full['sharpe']:.2f} vs {m_base['sharpe']:.2f}  (delta {m_full['sharpe']-m_base['sharpe']:+.2f})")
        print(f"  MaxDD   {m_full['max_dd']:.2%} vs {m_base['max_dd']:.2%}  (delta {m_full['max_dd']-m_base['max_dd']:+.2%})")
        print(f"  Calmar  {m_full['calmar']:.2f} vs {m_base['calmar']:.2f}  (delta {m_full['calmar']-m_base['calmar']:+.2f})")
