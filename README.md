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

# Crypto strategy (BTC / ETH) — C6 + Risk Controls v2

The XAU/USD v8 logic does **not** transfer to crypto (different macro drivers — DXY/TNX
filter rejects most crypto entries). A separate strategy was designed and tuned per asset.

## v2 = C6 + risk controls (current production)

v1 was a pure breakout (entry + MA exit only). v2 adds:
- **Volatility-targeted sizing**: position fraction = min(target_vol / realized_vol, leverage_cap).
  When realized 20-day daily vol is high (typical of drawdown periods), size shrinks
  automatically. When vol is low (often early-trend), size scales up to a cap.
- **Hard ATR stop** (ETH only): exit if low ≤ entry − 3 × ATR(14). ETH benefits;
  BTC does not (the regime filter is enough).

Result: **better on every metric for both assets**.

## v2 locked parameters

| Asset | dc_len | ma_exit | ma_long | vol_target | leverage_cap | hard_stop |
|---|---:|---:|---:|---:|---:|---:|
| **BTC** | 10 | 50 | 100 | 2.5 % daily | 3.0× | off |
| **ETH** | 30 | 50 | 150 | 3.0 % daily | 2.0× | 3 × ATR |

Margin / perp account uses the leverage_cap above. **Spot-only accounts** automatically
clamp to 1.0× — you still get the drawdown reduction, just less of the CAGR boost.

## v2 vs v1 results (full sample 2017-2026)

| Asset | Mode | CAGR | Sharpe | MaxDD | Calmar |
|---|---|---:|---:|---:|---:|
| BTC v1 (no risk controls) | — | 78.2 % | 1.50 | −52.8 % | 1.48 |
| **BTC v2 margin (3× cap)** | margin | **79.3 %** ↑ | **1.54** ↑ | **−42.1 %** ↑ | **1.88** ↑ |
| BTC v2 spot (1× cap) | spot | 65.8 % | 1.52 | −41.1 % | 1.60 |
| ETH v1 (no risk controls) | — | 48.2 % | 1.07 | −44.0 % | 1.09 |
| **ETH v2 margin (2× cap)** | margin | **51.6 %** ↑ | **1.14** ↑ | **−39.3 %** ↑ | **1.31** ↑ |
| ETH v2 spot (1× cap) | spot | 45.0 % | 1.11 | −36.9 % | 1.22 |
| Buy-and-hold BTC | — | 60.3 % | 1.03 | −83.4 % | 0.72 |
| Buy-and-hold ETH | — | 26.8 % | 0.71 | −94.0 % | 0.29 |

OOS 2023-2026: BTC margin 71% CAGR / 1.48 Sharpe / −24.7 % DD;
ETH margin 46% CAGR / 1.10 Sharpe / −29.7 % DD. **OOS held up vs in-sample.**

ETH profit factor jumped from 4.3 (v1) to **6.15 (v2)** — risk controls cut the
average-loss size dramatically.

## Files

### v1 (kept for reference)
- `engine/crypto_backtest.py`              — engine, no risk controls
- `strategies/run_crypto_compare.py`       — initial 6-variant comparison
- `strategies/tune_crypto.py`              — v1 grid search + walk-forward
- `strategies/final_crypto.py`             — v1 locked params

### v2 (current production)
- `engine/crypto_backtest_v2.py`           — engine with stops, trail stops, vol targeting
- `strategies/crypto_v2_compare.py`        — 11 risk-control variants tested
- `strategies/crypto_v2_tune.py`           — vol_target × leverage_cap × hard_stop grid + walk-forward
- `strategies/final_crypto_v2.py`          — **locked v2 params (production)**
- `strategies/Crypto_VolBreakout_v2.pine`  — TradingView Pine v5 with vol-targeted sizing
- `strategies/live_crypto_bot_v2.py`       — CCXT live bot with vol-targeted sizing + hard stop

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

# 3. Dry-run v2 bot (recommended first). Pick --mode spot for cash spot accounts,
#    or --mode margin for futures/perp accounts (allows up to 3x BTC / 2x ETH).
python strategies/live_crypto_bot_v2.py --asset BTC --exchange bybit --mode spot
python strategies/live_crypto_bot_v2.py --asset ETH --exchange bybit --mode spot

# 4. Testnet (Bybit/OKX have full sandboxes, free fake funds):
python strategies/live_crypto_bot_v2.py --asset BTC --exchange bybit --mode spot --testnet --live

# 5. Live (real money):
python strategies/live_crypto_bot_v2.py --asset BTC --exchange bybit --mode spot --live
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
