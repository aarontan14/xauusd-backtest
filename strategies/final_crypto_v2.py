"""Locked production crypto strategy v2: C6 vol-breakout + vol-targeted sizing
+ optional hard ATR stop. Best metrics on both BTC and ETH (full sample 2017-2026).

  BTC:  vol_target=0.025, leverage_cap=3.0, hard_stop=None
        CAGR 79.3%, Sharpe 1.54, MaxDD -42.1%, Calmar 1.88
        OOS 2023-2026: CAGR 71.3%, Sharpe 1.48, MaxDD -24.7%

  ETH:  vol_target=0.030, leverage_cap=2.0, hard_stop=3*ATR
        CAGR 51.6%, Sharpe 1.14, MaxDD -39.3%, Calmar 1.31, profit factor 6.15
        OOS 2023-2026: CAGR 45.5%, Sharpe 1.10, MaxDD -29.7%

NOTE on leverage_cap:
  - Spot-only accounts (Independent Reserve, plain Coinbase) cap at 1.0x.
    The vol-target still REDUCES drawdown but doesn't get the CAGR boost.
  - Margin / perp accounts (Bybit, OKX, Binance margin) can use 2-3x.
  - With cap=1.0, ETH gets ~Sharpe 1.09 / DD -29.8%; BTC ~Sharpe 1.52 / DD -41%.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
from crypto_backtest_v2 import load_crypto, signals_c6, run, buy_hold

# Strategy + risk-control parameters per asset
PARAMS = {
    "BTC": {
        "signal":      {"dc_len": 10, "ma_exit_len": 50, "ma_long_len": 100},
        "risk_margin": {"vol_target": 0.025, "leverage_cap": 3.0, "hard_stop_atr": None},
        "risk_spot":   {"vol_target": 0.025, "leverage_cap": 1.0, "hard_stop_atr": None},
    },
    "ETH": {
        "signal":      {"dc_len": 30, "ma_exit_len": 50, "ma_long_len": 150},
        "risk_margin": {"vol_target": 0.030, "leverage_cap": 2.0, "hard_stop_atr": 3.0},
        "risk_spot":   {"vol_target": 0.030, "leverage_cap": 1.0, "hard_stop_atr": 3.0},
    },
}


def report(asset: str, mode: str = "margin"):
    cfg = PARAMS[asset]
    risk = cfg["risk_margin"] if mode == "margin" else cfg["risk_spot"]
    df = load_crypto(asset)
    sig = signals_c6(df, **cfg["signal"])
    res = run(sig, **risk)
    bh = buy_hold(df)["metrics"]
    bh_calmar = bh["cagr"] / abs(bh["max_dd"]) if bh["max_dd"] else 0
    m = res["metrics"]

    print(f"\n=== {asset} C6+RC v2 [{mode}] ===")
    print(f"  Period:        {df.index.min().date()} -> {df.index.max().date()}  ({len(df)} bars)")
    print(f"  vol_target={risk['vol_target']}  leverage_cap={risk['leverage_cap']}  hard_stop_atr={risk['hard_stop_atr']}")
    print(f"  CAGR:          {m['cagr']:>7.2%}")
    print(f"  Sharpe:        {m['sharpe']:>7.2f}")
    print(f"  MaxDD:         {m['max_dd']:>7.2%}")
    print(f"  Calmar:        {m['calmar']:>7.2f}")
    print(f"  Trades:        {int(m['n_trades'])}")
    print(f"  Win rate:      {m['win_rate']:>7.2%}")
    print(f"  Profit factor: {m['profit_factor']:>7.2f}")
    print(f"  Exposure:      {m['exposure']:>7.2%}")
    print(f"  --- Buy-and-hold benchmark ---")
    print(f"  CAGR {bh['cagr']:.2%}  Sharpe {bh['sharpe']:.2f}  MaxDD {bh['max_dd']:.2%}  Calmar {bh_calmar:.2f}")
    return res


if __name__ == "__main__":
    for asset in ["BTC", "ETH"]:
        for mode in ["margin", "spot"]:
            report(asset, mode=mode)
