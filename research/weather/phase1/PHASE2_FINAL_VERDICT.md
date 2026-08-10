# Phase 2 Final Verdict — Hong Kong GEFS Exact-Bucket Thesis

Date: 2026-08-10
Branch: phase1-weather-calibration

## Final decision

**FAIL — do not deploy, do not forward-paper this exact thesis.**

The tested hypothesis was that Hong Kong highest-temperature 5–10¢ Polymarket buckets could be selected profitably using GEFS ensemble probabilities, station/grid bias correction, and a residual-convolution uncertainty model.

## Data integrity

- Phase 1 universe: 7,980 temperature events discovered.
- Valid resolved events: 7,964.
- Historical price observations: 396,029.
- Hong Kong Phase 2 manifest: 83 candidate buckets across 73 events, 10 winners.
- HKO actual daily maxima joined: 73/73.
- GEFS: 31-member ensemble, prior-day 18Z cycle, f006–f021, using only information available before the 08:00 HKT decision time.
- Bucket semantics were validated against HKO actual temperatures and Polymarket resolution labels: 83/83 checked, 0 mismatches.

## Important corrections made during research

1. Blind cheap-tail buying was rejected globally.
2. HKO deterministic forecast filtering did not outperform the blind candidate set.
3. Raw GEFS exact-bucket probabilities were initially mapped with `round()`, which was wrong for Polymarket's 0.1°C HKO resolution semantics. Exact N°C buckets map to [N.0, N+1.0). This was corrected and revalidated 83/83.
4. HKO Daily Extract was discovered to be served from a `.xml` URL whose body is actually JSON. This allowed recent actuals to be completed to 73/73.
5. Station/grid bias correction improved temperature MAE modestly but did not create robust probability edge.
6. Residual convolution was added to spread ensemble-member forecasts using only prior-date residuals, avoiding same-day outcome leakage.

## Phase 2E final gate

Eligible residual-convolution set:
- 67 rows
- 58 events/dates

Full-sample numbers were treated as contaminated diagnostics only and were **not** considered out-of-sample evidence.

### Chronological retrospective holdout

Train:
- 34 dates: 2026-04-28 through 2026-06-27

Test:
- 24 dates: 2026-07-03 through 2026-08-06

Threshold locked from TRAIN only:
- **15 percentage points**

Test all eligible:
- 27 rows
- 24 events
- 2 winners
- cost 2.100
- payout 2
- net **-0.100**
- ROI **-4.76%**

Test locked rule (`conv_edge > 15pp`):
- 8 rows
- 8 events
- **0 winners**
- cost 0.597
- payout 0
- net **-0.597**
- ROI **-100.00%**

### Test probability quality

Lower Brier is better.

- Polymarket market price Brier: **0.067624**
- Residual-convolution Brier: **0.084863**
- Residual-convolution Jeffreys Brier: **0.084904**
- Improvement vs market: **-0.017240**

The model was therefore worse calibrated than the market in the held-out period.

## Final gate

- `probability_pass = False`
- `economic_pass = False`
- **FINAL = FAIL**

## Interpretation

The research does **not** support deploying or forward-papering this specific Hong Kong + GEFS exact-bucket directional strategy. The historical in-sample profits were driven by selection effects/regime dependence and did not survive a chronological holdout. Even after station bias correction and empirical residual convolution, Polymarket's probabilities were better calibrated than the model on the test period.

This does **not** prove that all weather alpha is impossible. It only closes this tested thesis: **Hong Kong highest-temperature exact buckets selected by this GEFS-based probability stack at the tested 08:00 HKT horizon and 5–10¢ candidate regime.**

## Recommended next research direction

Do not tune this model further on the same 73-day universe. If weather research continues, use a new hypothesis and genuinely new forward data. Higher-priority alternatives from the broader trader reverse-engineering work are:

1. Weather market microstructure / stale-quote picking rather than directional fair-value prediction.
2. Complete-set / CTF / NegRisk inventory economics where applicable.
3. Maker/spread/rebate behavior separated from meteorological forecasting.
4. A new weather model only if it uses independent data/features and is evaluated on untouched post-cutoff dates from inception.

No live trading conclusion is permitted from this retrospective dataset.
