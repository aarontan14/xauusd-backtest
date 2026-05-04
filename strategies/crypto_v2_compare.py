"""
Test risk-control variants on top of C6 vol-breakout for BTC and ETH.

Variants:
  V1 baseline      - C6 with locked params (no stops, no vol target)
  V2 +hard         - hard ATR stop at entry
  V3 +trail        - chandelier trailing stop
  V4 +voltarget    - volatility-targeted position sizing
  V5 +kill         - max-loss kill switch
  V6 +tightexit    - tighter MA exit length
  V7 hard+trail    - hard stop AND trailing stop
  V8 hard+vol      - hard stop AND vol target
  V9 trail+vol     - trailing stop AND vol target
  V10 ALL          - hard + trail + vol target
  V11 hard+vol+kill- conservative composite
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import pandas as pd, numpy as np
from crypto_backtest_v2 import load_crypto, signals_c6, run, buy_hold

pd.set_option("display.float_format", lambda x: f"{x:.4f}")
pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 20)

PARAMS = {
    "BTC": {"dc_len": 10, "ma_exit_len": 50, "ma_long_len": 100},
    "ETH": {"dc_len": 30, "ma_exit_len": 50, "ma_long_len": 150},
}

# Tighter exit for V6 — shorter ma_exit
TIGHT_EXIT = {"BTC": 30, "ETH": 30}


def configs(asset: str):
    p = PARAMS[asset]
    pt = {**p, "ma_exit_len": TIGHT_EXIT[asset]}
    return [
        ("V1_baseline",    p,  dict()),
        ("V2_hard",        p,  dict(hard_stop_atr=4.0)),
        ("V3_trail",       p,  dict(trail_stop_atr=4.0)),
        ("V4_voltarget",   p,  dict(vol_target=0.025, leverage_cap=1.0)),
        ("V5_kill",        p,  dict(max_loss_dd=-0.20)),
        ("V6_tightexit",   pt, dict()),
        ("V7_hard+trail",  p,  dict(hard_stop_atr=5.0, trail_stop_atr=5.0)),
        ("V8_hard+vol",    p,  dict(hard_stop_atr=4.0, vol_target=0.025)),
        ("V9_trail+vol",   p,  dict(trail_stop_atr=4.0, vol_target=0.025)),
        ("V10_ALL",        p,  dict(hard_stop_atr=4.0, trail_stop_atr=5.0, vol_target=0.025)),
        ("V11_safe",       p,  dict(hard_stop_atr=3.0, vol_target=0.02, max_loss_dd=-0.20)),
    ]


for asset in ["BTC", "ETH"]:
    df = load_crypto(asset)
    print(f"\n{'='*100}\n{asset} risk-control variants  ({df.index.min().date()} -> {df.index.max().date()}, {len(df)} bars)\n{'='*100}")
    rows = []
    for name, sig_p, run_p in configs(asset):
        sig = signals_c6(df, **sig_p)
        res = run(sig, **run_p)
        m = res["metrics"]
        rows.append({"variant": name, **m})

    bh = buy_hold(df)["metrics"]
    bh["calmar"] = bh["cagr"] / abs(bh["max_dd"]) if bh["max_dd"] else 0
    rows.append({"variant": "buy_hold",
                 "total_return": bh["total_return"], "cagr": bh["cagr"], "sharpe": bh["sharpe"],
                 "max_dd": bh["max_dd"], "calmar": bh["calmar"],
                 "n_trades": 1, "win_rate": 1.0, "profit_factor": float("inf"),
                 "avg_hold": len(df), "exposure": 1.0})

    cmp = pd.DataFrame(rows).set_index("variant")
    cols = ["cagr", "sharpe", "max_dd", "calmar", "n_trades", "win_rate", "profit_factor", "exposure"]
    print(cmp[cols])

    # Highlight winners
    print(f"\n{asset} winners:")
    print(f"  Best Sharpe : {cmp['sharpe'].idxmax()}    ({cmp['sharpe'].max():.2f})")
    print(f"  Best Calmar : {cmp['calmar'].idxmax()}    ({cmp['calmar'].max():.2f})")
    print(f"  Best CAGR   : {cmp['cagr'].idxmax()}    ({cmp['cagr'].max():.2%})")
    print(f"  Smallest DD : {cmp['max_dd'].idxmax()}    ({cmp['max_dd'].max():.2%})")
