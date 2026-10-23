---
name: kiyotaka-trade-replay
description: "Use when replaying BTC/crypto trades with Greg's Kiyotaka-style chart pack: multi-timeframe Fib/Q-levels, RSI, Bollinger Bands, ATR, VWAP, volume, fill markers, and callouts for add/hold/trim/exit decisions."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [trading, replay, bitcoin, fibonacci, q-levels, kiyotaka, bollinger, rsi, hyperliquid]
    related_skills: []
---

# Kiyotaka Trade Replay

Use this skill when Greg asks to replay a crypto trade, improve active-trade reporting, inspect whether price respected Kiyotaka/Fibonacci/Q levels, or produce a chart gallery across multiple timeframes.

This skill is **analysis-only by default**. Do not place orders, alter brackets, or enable live execution while using it unless Greg explicitly asks for a separate execution workflow.

## Trigger phrases

- "replay yesterday's trade"
- "Kiyotaka indicators"
- "draw fib levels"
- "Q levels"
- "0.635 / 0.562 / 0.368"
- "same chart in different timeframes"
- "gallery"
- "RSI on each timeframe"
- "Bollinger bands"
- "callouts"
- "active trade status every five minutes"

## Required outputs

Session-specific chart-gallery requirements are captured in `references/session-2026-09-btc-chart-gallery-requirements.md`; use that reference when generating BTC/crypto replay chart packs for Greg.

For a serious replay report, produce both:

1. **Chart gallery**
   - Same trade window across several timeframes.
   - Fill markers on every chart.
   - Fib/Q-level grid on every chart.
   - RSI and Bollinger context on every chart or companion panel.
   - Callouts explaining what the trader should have seen.

2. **Decision audit**
   - Entry quality.
   - Add quality.
   - No-add zones.
   - Trim/protect zones.
   - Reversal warning line.
   - ATR stop candidate.
   - VWAP/volume confirmation or failure.

## Default timeframe gallery

Use these unless Greg specifies otherwise:

- **1m execution chart** — exact fill timing, slippage, local RSI.
- **5m decision chart** — main active-trade feed timeframe.
- **15m structure chart** — swing/Fib anchors and reversal zones.
- **1h context chart** — major levels, trend state, higher-timeframe RSI/Bollinger.

Optional:

- 4h only when the trade lasted long enough or the higher-timeframe swing clearly matters.
- 30m when 15m is too noisy but 1h is too coarse.

## Chart requirements

Every chart should include:

- BTC candles only unless Greg expands scope.
- Entry/add/close markers.
- Average entry line when reconstructable.
- Realized exit line if closed.
- Standard Fib retracement levels.
- Kiyotaka/Q levels.
- Right-side volume profile for that timeframe/window.
- POC / VAH / VAL when enough volume-at-price data exists.
- Bollinger Bands.
- RSI value/state for that timeframe.
- ATR stop or ATR stretch annotation.
- VWAP where intraday/session data supports it.
- Volume bars or volume z-score markers.
- Text callouts for obvious mistakes or opportunities.

If the chart is too dense, split into two images:

- Price chart: candles, fills, VWAP, Bollinger, Fib/Q levels, ATR stop.
- Indicator panel: RSI, volume z-score, ATR/stretched status.

## Fib and Kiyotaka/Q levels

Always include standard Fib levels:

- `0.236`
- `0.382`
- `0.500`
- `0.618`
- `0.786`
- `1.000`

Always include custom Kiyotaka/Q levels:

- `0.368`
- `0.562`
- `0.635`

Optional extensions:

- `1.272`
- `1.618`
- `2.000`

Label custom levels distinctly from standard Fib levels. Example labels:

- `Q 0.368`
- `Q 0.562`
- `Q 0.635`
- `Fib 0.618`

## Anchor selection

For each timeframe, choose anchors independently:

1. Detect recent confirmed swing high/low with pivot lookback appropriate to timeframe.
2. Require swing size to exceed a minimum ATR multiple.
3. For longs after a rally, draw retracement from swing low to swing high.
4. For reversal/failure analysis, also test high to low if price has broken down.
5. If multiple plausible anchors exist, show the active anchor values in the report and explain why the chosen anchor matters.

Default pivot settings:

- 1m: pivot leg 5-8, min swing 1.5 x ATR.
- 5m: pivot leg 6-10, min swing 2.0 x ATR.
- 15m: pivot leg 6-12, min swing 2.5 x ATR.
- 1h: pivot leg 4-8, min swing 2.5-3.0 x ATR.

## Indicators

Compute per timeframe:

- RSI 14.
- Bollinger Bands 20, 2 standard deviations.
- ATR 14.
- VWAP/session VWAP where applicable.
- EMA 9/21 for momentum state.
- Volume z-score over 50 bars.

Useful states:

- RSI overheated: `>70`; very overheated: `>75`.
- RSI weak: `<45`; bearish: `<40`.
- Bollinger upper-band chase: close near/above upper band after extended run.
- Bollinger mean reversion risk: rejection outside band followed by close back inside.
- ATR stretch: distance from VWAP/EMA exceeds 1.5-2.0 x ATR.
- Volume failure: breakout candle high volume, continuation candle lower volume, then rejection.

## Decision labels

Every fill gets one label:

- `Good entry`
- `Acceptable starter`
- `Late chase`
- `No-add zone`
- `Protect-only zone`
- `Trim signal`
- `Exit warning`
- `Reversal confirmed`

Use concise callouts on charts:

- "No add: RSI 76 + upper BB + Q0.635 resistance"
- "Protect: VWAP stretched 1.8x ATR"
- "Trim: wick rejection at Fib 0.618/Q0.635 cluster"
- "Exit watch: close below ATR trail"
- "Reversal confirmed: lost VWAP + lower high"

## Active-trade 5-minute feed

Only send when an active position exists.

Default Telegram message:

```text
BTC Long: HOLD / TRIM / NO ADD

Price: <mark>
Avg: <avg>
PnL: <unrealized>
RSI 5m: <value/state>
BB 5m: <middle/upper/lower state>
ATR stop: <level>
VWAP: <above/below/stretched>
Q/Fib: <nearest level + distance>
Volume: <confirming/fading>

Action:
- <one decisive instruction>
- <one invalidation level>
```

Include higher-timeframe context when it changes the action:

- `15m RSI overheated`
- `1h at upper Bollinger`
- `15m Q0.635 resistance overhead`
- `1h Fib 0.618 support below`

## Replay workflow

1. Pull fills from local guardian log and/or Hyperliquid public `userFillsByTime`.
2. Fetch OHLCV candles for 1m, 5m, 15m, and 1h over the trade window plus context buffer.
3. Reconstruct position state: entries, adds, average entry, close, realized/unrealized PnL.
4. Compute indicators per timeframe.
5. Detect swing anchors and draw Fib/Q levels per timeframe.
6. Generate gallery PNGs.
7. Write a concise decision audit.
8. If requested, schedule the 5-minute active-trade feed.

## Report format

Start with the answer:

- Was the add good or bad?
- What should have happened instead?
- Which level/indicator gave the earliest warning?

Then show evidence:

- Chart gallery.
- Fill-by-fill audit.
- Indicator states at fill time.
- Counterfactual: best trim/stop/no-add action.

## Safety boundaries

- Read-only analysis can use public account/fill APIs and local logs.
- Do not request or print private keys.
- Do not enable `place_enabled`, `auto_brackets`, or reduce-only order placement.
- Keep live execution behind a separate explicit approval gate.
- Label outputs as analytical decision support, not financial advice.

## Common pitfalls

1. **Chart too zoomed out.** Greg needs zoomed trade windows. Use time buffers around fills rather than whole-day charts only.
2. **No levels on gallery charts.** Every timeframe chart must redraw its own Fib/Q grid.
3. **Single-timeframe bias.** A 1m add can look okay while 15m/1h says no-add. Show both.
4. **Fib anchors hidden.** Always report anchor low/high and direction.
5. **Indicators without callouts.** The point is the decision. Add direct labels like `No add`, `Trim`, `Exit watch`.
6. **Overloaded image.** Split price/indicator panels if needed; do not make one unreadable chart.
7. **Leverage-first thinking.** Determine invalidation and stop distance before discussing leverage.

## Expert agent spec

If creating a dedicated worker/agent for this workflow, its role is:

**Name:** `trade-replay-analyst`

Responsibilities:

- Pull read-only trade logs and candles.
- Generate multi-timeframe Kiyotaka/Fib/Q chart galleries.
- Compute RSI, Bollinger, ATR, VWAP, volume z-score.
- Produce add/hold/trim/exit decision audits.
- Emit 5-minute active-trade status summaries.

Must not:

- Place live orders.
- Read or print private keys.
- Change guardian execution config.
- Claim edge without replay evidence.

## Verification checklist

- [ ] Fills loaded and timestamps normalized.
- [ ] 1m, 5m, 15m, 1h candles loaded.
- [ ] Each chart has fill markers.
- [ ] Each chart has Fib/Q levels.
- [ ] Each chart has RSI/Bollinger context.
- [ ] Callouts identify add/no-add/trim/exit zones.
- [ ] Report names the earliest reversal warning.
- [ ] Feed is silent when no active trade exists.
- [ ] Feed remains read-only unless explicitly upgraded.
