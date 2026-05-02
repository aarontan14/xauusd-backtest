# XAU/USD MacroTrend v8 — Strategy & Runbook

A long-only, daily-timeframe gold strategy built around **buying pullbacks during
confirmed macro bull regimes** (weakening dollar + falling real-yield proxy +
secular uptrend) and exiting on regime breaks.

## Backtest summary (2010-01-04 → 2026-05-01)

Daily bars on `GC=F` (COMEX gold front-month, used as XAU/USD proxy), with realistic
0.30 USD spread + 0.05 USD slippage and 4% APY interest accrued on cash while flat.

| Variant | CAGR | Sharpe | Max DD | Calmar | Trades | Win % | PF |
|---|---:|---:|---:|---:|---:|---:|---:|
| **v8 @ 1.0x leverage** | **7.14 %** | **1.27** | **−6.9 %** | **1.03** | 119 | 49.6 % | 2.65 |
| v8 @ 1.5x leverage | 8.85 % | 1.07 | −11.3 % | 0.78 | 119 | 49.6 % | 2.68 |
| v8 @ 2.0x leverage | 10.47 % | 0.97 | −16.4 % | 0.64 | 119 | 49.6 % | 2.70 |
| Buy-and-hold benchmark | 9.10 % | 0.60 | −44.4 % | 0.21 | 1 | — | — |

**Walk-forward** (train 2010-2018, test 2019-2026 unseen):
- In-sample: 6.30 % CAGR, 1.22 Sharpe, −6.9 % DD
- **Out-of-sample: 8.49 % CAGR, 1.39 Sharpe, −6.8 % DD** ← real edge, not overfit

The strategy beats buy-and-hold on Sharpe (1.27 vs 0.60) and on max drawdown by
**6×**. Absolute returns lag buy-and-hold at 1x because the strategy is in cash
~87 % of the time — that's why modest leverage (1.5×) closes the absolute gap
while still keeping DD at a third of buy-and-hold.

## How the signal works

**Entry — all of:**
1. DXY 60-day % change < 0  (dollar weakening)
2. 10Y yield (TNX) 60-day change < 0  (real-yield proxy falling, gold-bullish)
3. Close > 200-day MA  (secular uptrend intact)
4. RSI(14) < 50  (not overbought)
5. Close ≤ 10-day MA × 1.05  (mild pullback to short-term mean)

**Exit — any of:**
- Close < 50-day MA  (regime break)
- RSI(14) > 75  (parabolic exhaustion)
- Hard ATR stop at entry − 3 × ATR(14)  (protective)

**Sizing:** 100 % of equity per trade at 1× leverage, no pyramiding, single position.

---

## Repo layout

```
data/                 — downloaded daily bars (gold + DXY + TNX + VIX + SPX)
engine/backtest.py    — vectorized engine + 8 strategy variants + buy-hold
strategies/
  run_compare.py      — runs all 8 variants, prints comparison
  tune_v8.py          — 729-combo grid search + walk-forward selection
  final_v8.py         — locked v8, leverage sweep, OOS validation
  XAUUSD_MacroTrend_v8.pine  — TradingView strategy (Pine v5)
  live_ibkr_bot.py    — daily-cron IBKR auto-trader (ib_insync)
  tv_webhook_bridge.py — Flask webhook receiver: TV alert -> IBKR
```

## Reproducing the backtest

```bash
pip install pandas numpy yfinance matplotlib
python strategies/run_compare.py    # all 8 variants vs buy-hold
python strategies/tune_v8.py        # grid search + walk-forward
python strategies/final_v8.py       # locked params + leverage sweep
```

---

## Deployment options

### A) TradingView (chart + manual or alert-driven)

1. Open TradingView, load `OANDA:XAUUSD` or `COMEX:GC1!` on **Daily**.
2. Pine Editor → paste `strategies/XAUUSD_MacroTrend_v8.pine` → Add to chart.
3. Click "Strategy Tester" to see the backtest from TV's data.
4. To get alerts: right-click chart → Add Alert → choose "XAUUSD MacroTrend v8" →
   condition "Entry alert" / "Exit alert" → set webhook URL + JSON message
   already embedded in the script.

### B) IBKR auto-trader, daily polling (simplest)

Best for Singapore retail. IBKR offers XAUUSD CFD, MGC (micro gold) and GC futures.

```bash
pip install ib_insync yfinance pandas numpy

# 1. Start TWS or IB Gateway. Enable API in Configuration → API → Settings.
# 2. Paper-test first:
python strategies/live_ibkr_bot.py --instrument CFD --port 7497 --leverage 1.0
# (no --live flag → DRY RUN; logs the signal & intended order size)

# 3. Once satisfied, run live (TWS port 7496, gateway 4001):
python strategies/live_ibkr_bot.py --instrument CFD --port 7496 --account U1234567 --leverage 1.0 --live

# 4. Schedule once a day (after gold closes ~ 06:00 SGT). On Windows:
schtasks /create /tn "XAUUSD_v8" /tr "python ...live_ibkr_bot.py ... --live" /sc daily /st 06:30
```

### C) TradingView → webhook → IBKR (event-driven)

```bash
pip install flask ib_insync
python strategies/tv_webhook_bridge.py --secret MY_LONG_SECRET --instrument CFD --leverage 1.0
# Expose with ngrok:
ngrok http 8080
# Take the HTTPS URL, paste into TradingView alert webhook.
# In the alert, also add a custom HTTP header:  X-Auth-Token: MY_LONG_SECRET
```

---

## Singapore broker reality check

| Broker | Auto-trade XAU/USD? | How |
|---|---|---|
| **IBKR** (recommended) | ✅ Yes — XAUUSD CFD, MGC/GC futures | TWS API via `ib_insync` (used here) |
| **OANDA** | ✅ Yes — spot XAU/USD CFD | REST API; could port `live_ibkr_bot.py` to it |
| **Tiger Trade** | ❌ Not for spot gold | TigerOpen API supports HK/US stocks, options, futures — not XAU/USD CFD |
| **Moo Moo / Futu** | ❌ Not for spot gold | Same — equities/options/futures only |
| **FOREX.com SG** | ✅ Yes — XAU/USD | REST API, similar pattern |

If you must use Tiger or Moo Moo, the closest auto-tradable proxy is **GLD** ETF
or **GC** futures (US futures permission required). The strategy generalises but
spread/cost characteristics differ — re-tune.

---

## Important caveats — read before risking money

1. **Past performance is not future returns.** This is a 16-year backtest in
   what was overwhelmingly a gold bull market. If real yields rise persistently,
   gold's macro backdrop changes and this signal will produce far fewer trades —
   possibly with worse hit rates.
2. **Single asset, single regime.** No survivorship bias here, but also no
   cross-asset robustness check. Consider running the same logic on silver, BTC,
   or commodity baskets to gauge whether the macro filter is doing real work.
3. **Slippage assumption (0.05 USD/oz).** Realistic for IBKR CFD or futures.
   On rolled-spread retail brokers (some MT4/MT5 shops in SG), spreads can be
   1–3 USD — that would meaningfully erode the edge.
4. **Data source = `GC=F` futures.** Spot XAUUSD differs by a basis (cost of
   carry). Should be small at daily frequency but verify when going live.
5. **Leverage is dangerous.** Each 1× of leverage scales both return AND
   drawdown. A −20 % strategy DD becomes a margin-call event at 5×. **Start at
   1×.** Only step up after at least 6 months of paper trading.
6. **No tax or borrow cost modelled.** CFD financing is roughly Fed Funds + 1.5 %
   per year on the open notional. At 1× leverage with 13 % time-in-market this
   is ~0.7 % drag/yr — small. At 3× it becomes ~2 %/yr, which the table above
   does not subtract.
7. **Macro regime can shift.** The DXY/TNX filter assumes the post-2010
   relationship holds. If the Fed runs structurally higher real rates and gold
   trades with rising yields (regime change), this signal will misfire. Review
   the regime fit annually.

---

## Recommended starting point

- **Capital:** anything ≥ $10k for IBKR CFD, ≥ $20k for MGC futures.
- **Leverage:** 1.0× (max 1.5×) for the first 12 months.
- **Cadence:** check signal daily after NY close (~ 5:00–6:00 SGT).
- **Manual override rule:** if you hit −10 % drawdown, stop the bot, review the
  regime, decide consciously whether to resume.
