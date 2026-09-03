# Author review of the reproduction

On **2026-08-12** the team walked the paper's author, Ayman Nassar, through
this workflow and the reproduction results in [`PAPER_COMPARISON.md`](PAPER_COMPARISON.md)
(paper: Nassar et al. 2026, *Environmental Modelling & Software* 203, 107031,
[doi:10.1016/j.envsoft.2026.107031](https://doi.org/10.1016/j.envsoft.2026.107031)).
This page records his verdict and the changes he requested, and tracks their
status.

## Verdict

**The reproduction stands.** The author confirmed that small deviations in
calibration results are expected: DDS is stochastic, and this workflow runs
calibration as parallel seeded trials (`--dds-trials`) where the paper ran a
single sequential trajectory. Minor metric differences between the two are
inherent to the algorithm, not a defect in the reproduction.

He also confirmed the experiment windows as implemented: of the 4-year period,
the first two years initialize the model and the last two calibrate it —
matching this workflow's `--training-start` split.

**All three requested changes are now implemented, and #1 and #2 are validated
by run0003 (2026-09-03, 49/49 Success).** The calibrated result on the
corrected hourly path is **KGE 0.8903 / NSE 0.7914 against the published
0.893 / 0.785** — a 0.003 KGE difference, now like-for-like with Fig. 10
rather than mixed-resolution. Details in
[`PAPER_COMPARISON.md`](PAPER_COMPARISON.md).

## Requested changes

| # | Feedback | What was wrong | Status |
|---|----------|----------------|--------|
| 1 | **Calibration must follow the model run**, not branch off data assembly. The paper's sequence is data prep → baseline model run → calibration → calibrated run. | `workflow_generator.py` had `calibrate` and `apply_params` consuming `assemble_rundir`'s tar, making them siblings of `run_nextgen` rather than its descendants. | **Implemented 2026-08-31, validated 2026-09-03**: both jobs now consume `rundir_{gage}_run.tar` from `run_nextgen`. run0003 ran the re-parented DAG to 49/49 Success in the paper's prep → baseline run → calibrate → calibrated run sequence. |
| 2 | **Evaluation must be hourly**, matching the paper's Fig. 10 metrics. | The model run and the calibration objective were already hourly, but the evaluation path was daily: `bin/fetch_usgs_obs.py` pulled NWIS *daily* values, and `bin/outputs_analysis.py` / `bin/teehr_evaluation.py` aggregated the hourly simulation to daily means before scoring. | **Implemented 2026-08-31**: obs are fetched hourly (instantaneous values resampled; daily-values fallback with a warning), and both evaluation jobs score hourly on timestamp-aligned joins, reporting the daily aggregate alongside (`resolution` column / `kge_daily`). **Validated 2026-09-03**: run0003 metrics carry `kge_resolution=hourly` with 34,994 of 35,040 hours compared, and the calibrated evaluation-period hourly KGE is 0.8903 vs the published 0.893. |
| 3 | **Workflow diagram** should be restructured to show the data-preparation → model-setup/run → calibration sequence (follows from #1). The author offered to help revise the flowchart. | README pipeline diagram / slide-deck figures. | README diagram updated 2026-08-31; slide-deck figures still open. |

## Discussion notes

- The reproduction's headline result (calibrated hourly KGE 0.898 vs the
  published 0.893, see `PAPER_COMPARISON.md`) was presented alongside the
  daily-aggregated evaluation metrics; feedback #2 removes that mixed
  resolution so every reported number is like-for-like with the paper.
- The parallel-trials design (#1's branch shape) was discussed explicitly:
  a DDS trajectory is inherently sequential, so parallelism comes from
  independent seeded trials plus a reducer. The author's concern is the
  *position* of calibration in the DAG, not the multi-start strategy itself.
- This review feeds an AGU presentation and a planned paper on AI methods for
  hydrological workflows.

### The 4-year split, and what it implies about the headline number

The author's window clarification — *"the first two years were used for model
initialization and the next two years for calibration"* — is a confirmation of
what this workflow already did, not a change request. run0003 implements it
exactly:

| Window | Dates | Hours compared |
|---|---|---|
| Full run | 2017-10-01 → 2021-09-30 | 35,040 timesteps (4 water years) |
| Initialization / spin-up | 2017-10-01 → 2019-10-01 | 17,474 |
| Calibration | 2019-10-01 → 2021-09-30 | 17,520 |

This also settles a discrepancy that had been open since the conversion: the
paper text says `training_start = 2019-10-01` (a 2+2 split) while the
published notebooks say `2020-10-01` (which would be 3+1). The paper text was
treated as authoritative; the author's verbal 2+2 description confirms that
choice, so it is no longer a judgment call.

**Consequence worth stating plainly.** `bin/lib/cal_utils.py` windows the
observed series to `>= training_start_date`, so DDS optimizes its objective
over 2019-10 → 2021-09 — the *same* window the metrics label
`period=evaluation`. The headline 0.8903 (and the paper's 0.893) is therefore
**in-sample calibration performance; this experimental design has no held-out
validation window.** That is the paper's methodology and reproducing it
faithfully is correct, but the distinction should be stated before an audience
asks. The spin-up rows are not a substitute: parameters calibrated on WY2020–21
score KGE 0.165 on WY2018–19, but that window is also the model's warm-up, so
the low score mixes parameter transferability with unstable initial states. A
genuine validation number would need a third window outside both — a scope
extension beyond the paper, not a correction to it.

All three requested changes have landed; only the slide-deck figures for #3
remain open.
