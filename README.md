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

---

# Crypto strategy (BTC / ETH) — C6 vol-weighted Donchian breakout

The XAU/USD v8 logic does **not** transfer to crypto (different macro drivers — DXY/TNX
filter rejects most crypto entries). A separate strategy was designed and tuned per asset.

## C6 design

**Entry — both:**
1. Close ≥ N-day rolling high (Donchian breakout)
2. Close > L-day moving average (long-term trend filter)

**Exit:**
- Close < M-day moving average

That's it. Pure trend-follower designed for crypto's long, persistent trends.

## Locked parameters (walk-forward selected, train ≤ 2022, test 2023-2026)

| Asset | dc_len | ma_exit | ma_long | OOS Sharpe | OOS Calmar | Full CAGR | Full Sharpe | Full DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **BTC** | 10 | 50 | 100 | **1.35** | 1.96 | **78.1 %** | 1.50 | −52.8 % |
| **ETH** | 30 | 50 | 150 | **1.00** | 1.41 | **48.2 %** | 1.07 | −44.0 % |
| Buy-hold BTC | — | — | — | — | — | 59.3 % | 1.02 | −83.4 % |
| Buy-hold ETH | — | — | — | — | — | 26.7 % | 0.71 | −94.0 % |

Both beat buy-and-hold on **CAGR, Sharpe AND drawdown** simultaneously. ETH stats are
particularly good: 70 % win rate, profit factor 4.3.

## Files

- `engine/crypto_backtest.py`        — engine + strategy variants for BTC/ETH
- `strategies/run_crypto_compare.py` — compares v8-gold dropped on crypto + 6 crypto variants vs buy-hold
- `strategies/tune_crypto.py`        — grid search + walk-forward per asset
- `strategies/final_crypto.py`       — locked params, leverage sweep
- `strategies/Crypto_VolBreakout.pine` — TradingView Pine v5 with BTC/ETH presets
- `strategies/live_crypto_bot.py`    — CCXT-based live trader (Bybit/OKX/IndependentReserve/etc.)

## Singapore broker reality for crypto

| Platform | Auto-trade BTC/ETH? | API fit | SG legal status |
|---|---|---|---|
| **Independent Reserve** | ✅ spot BTC/ETH | CCXT-supported | **MAS-licensed** — cleanest path |
| **Bybit** | ✅ spot + perps + options | excellent CCXT support | accessible from SG, not MAS-licensed for retail |
| **OKX** | ✅ spot + perps | excellent CCXT support | accessible from SG, not MAS-licensed |
| **Coinbase** | partial | CCXT-supported | limited SG availability |
| **Binance** | API works for grandfathered users | CCXT | binance.com is **not licensed in SG** for new accounts; binance.sg shut down |
| **Crypto.com** | ✅ spot | CCXT-supported | check current MAS status |
| **IBKR** | ✅ via crypto **ETFs** (IBIT, FETH, ETHA, BITO) | reuse `live_ibkr_bot.py` | already your existing setup |
| **Tiger Trade / Moo Moo** | ✅ via crypto ETFs (IBIT, FETH) | their APIs are stocks/options/ETFs only | accessible |

**Recommended paths:**
1. **Cleanest legal SG path:** Independent Reserve — set `--exchange independentreserve`
   on `live_crypto_bot.py`.
2. **Best liquidity / fees / API:** Bybit or OKX — set `--exchange bybit` etc.
3. **If you'd rather stay inside your existing IBKR / Tiger / Moo Moo setup:** trade
   the crypto ETFs (IBIT for BTC, FETH/ETHA for ETH). The signal still applies — just
   feed your `live_ibkr_bot.py` a different contract. Note: ETFs trade only during US
   market hours (21:30 – 04:00 SGT), so signals computed at UTC midnight will execute
   the next NY session open. Slightly worse fills than spot crypto.

## Live trading

```bash
pip install ccxt yfinance pandas numpy

# 1. Create API key on your exchange (READ + TRADE only — DO NOT enable WITHDRAW).
# 2. Export creds (use your shell's env-var setter):
#      Linux/Mac:  export BYBIT_API_KEY=...
#                  export BYBIT_API_SECRET=...
#      Windows PowerShell:  $env:BYBIT_API_KEY = "..."
#                           $env:BYBIT_API_SECRET = "..."

# 3. Dry-run (recommended first):
python strategies/live_crypto_bot.py --asset BTC --exchange bybit
python strategies/live_crypto_bot.py --asset ETH --exchange bybit

# 4. Testnet (Bybit/OKX have full sandboxes, free fake funds):
python strategies/live_crypto_bot.py --asset BTC --exchange bybit --testnet --live

# 5. Live (real money):
python strategies/live_crypto_bot.py --asset BTC --exchange bybit --live
```

Schedule daily after UTC midnight (e.g. 09:00 SGT = 01:00 UTC), same pattern as the
gold bot. The C6 entry signal triggers maybe 5–10 times per year per asset, so a
daily check is plenty.

## Crypto-specific caveats

1. **Spot leverage warning.** The Pine + Python both default to 1.0x. Crypto is
   already 5-10× more volatile than gold. Crypto perps offer 50-100× leverage —
   don't. Even 2× on this strategy turns a −44 % DD into −60 %+, and you can be
   liquidated before the regime exit fires.
2. **Exchange counterparty risk.** A blown-up exchange (FTX-style) wipes your
   capital regardless of strategy. Spread across exchanges; never keep more than
   you can afford to lose on a single venue.
3. **API key scope.** Always disable withdraw permission on bot keys.
4. **Slippage in fast moves.** Backtest assumes 5 bps; in panic crashes you can
   pay 50–200 bps. Stop-loss orders especially.
5. **Drawdown is structural.** The −53 % BTC DD happened during the 2022 bear.
   Do not run this strategy with money you'll need within 24 months.
6. **Yfinance daily data quirks.** BTC/ETH on yfinance use a 24h close at
   ~00:00 UTC. Live exchange will use exchange-local close. Differences in close
   timing can shift signal by 1 day occasionally. For tighter execution, swap
   yfinance for the exchange's own daily kline endpoint (CCXT: `fetch_ohlcv`).

## Recommended starting point (crypto)

- **Capital:** start small — $1k-$5k per asset is enough to validate execution.
- **Leverage:** **1.0× spot only** for the first 6 months. Step up only after you
  see the strategy survive a real drawdown.
- **Asset split:** if running both, split capital ~60/40 BTC/ETH (BTC has the
  better risk-adjusted profile).
- **Cadence:** daily check after exchange daily close (varies by exchange — easiest
  to use 00:00 UTC).
- **Halt rule:** if real account drawdown breaches −20 %, pause the bot and review.
  Real DD is usually worse than backtest because of execution friction.
