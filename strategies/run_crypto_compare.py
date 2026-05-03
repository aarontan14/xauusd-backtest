"""Compare strategies on BTC and ETH."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import pandas as pd
from crypto_backtest import (
    load_crypto, run_crypto, buy_hold_crypto,
    strat_v8_gold, strat_c1_trend, strat_c2_donchian, strat_c3_mr,
    strat_c4_dual_ma, strat_c5_hybrid, strat_c6_volwf,
)

pd.set_option("display.float_format", lambda x: f"{x:.4f}")
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)

variants = [
    ("v8_gold",      strat_v8_gold),
    ("C1_trend",     strat_c1_trend),
    ("C2_donchian",  strat_c2_donchian),
    ("C3_mr",        strat_c3_mr),
    ("C4_dual_ma",   strat_c4_dual_ma),
    ("C5_hybrid",    strat_c5_hybrid),
    ("C6_volwf",     strat_c6_volwf),
]

for asset in ["BTC", "ETH"]:
    df = load_crypto(asset)
    print(f"\n{'='*70}\n=== {asset}  ({df.index.min().date()} -> {df.index.max().date()}, {len(df)} bars) ===\n{'='*70}")
    rows = []
    for name, fn in variants:
        sig = fn(df)
        res = run_crypto(sig, leverage=1.0)
        m = res["metrics"]
        rows.append({"strategy": name, **m})

    bh = buy_hold_crypto(df)
    m = bh["metrics"]
    rows.append({"strategy": "buy_hold",
                 "total_return": m["total_return"], "cagr": m["cagr"], "sharpe": m["sharpe"],
                 "max_dd": m["max_dd"],
                 "calmar": m["cagr"]/abs(m["max_dd"]) if m["max_dd"] else 0,
                 "n_trades": 1, "win_rate": 1.0, "profit_factor": float("inf"),
                 "avg_hold": len(df), "exposure": 1.0})

    cmp = pd.DataFrame(rows).set_index("strategy")
    cols = ["total_return","cagr","sharpe","max_dd","calmar","n_trades","win_rate","profit_factor","exposure"]
    print(cmp[cols])
