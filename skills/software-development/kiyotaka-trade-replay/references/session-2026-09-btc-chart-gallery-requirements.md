# Session-derived BTC chart gallery requirements

Context: Greg reviewed early BTC replay charts and said they needed more detail and better zoom. This reference captures the reusable requirements for future trade replay reports.

## Chart gallery expectations

Produce a gallery of the same trade window across timeframes, not one generic whole-day chart.

Default gallery:

- `1m` — execution/fill timing.
- `5m` — active-trade decision frame.
- `15m` — structure and local swing anchors.
- `1h` — broader context and major support/resistance.

Use `4h` only when the trade lasted long enough or the major swing needs it. Prefer zoomed windows around the fills plus context buffers.

## Required overlays on every timeframe

BTC-only until Greg expands the scope again. Do not mix ETH/ZEC/other symbols into the gallery.

- Fill markers for entry/add/close.
- Average entry line when reconstructable.
- Fib retracement levels on that timeframe's own anchor.
- Kiyotaka/Q levels on that timeframe's own anchor.
- Visible volume profile on each timeframe, ideally right-side horizontal volume-at-price bars.
- Mark volume profile POC / value area high / value area low when enough data exists.
- RSI state for that timeframe.
- Bollinger Bands for that timeframe.
- ATR/VWAP/volume context when supported by the data.
- Callouts that state the decision implication, not just indicator values.

## Callout examples

- `No add: RSI overheated + upper Bollinger + Q0.635 resistance`.
- `Trim: wick rejection at Fib/Q cluster`.
- `Exit watch: VWAP lost and ATR trail close`.
- `Reversal: lower high after upper-band rejection`.

## Pitfall to avoid

Do not send charts that merely prove candles were plotted. Greg wants readable decision-support charts: zoomed, multi-timeframe, level-rich, and annotated with what should have been done.
