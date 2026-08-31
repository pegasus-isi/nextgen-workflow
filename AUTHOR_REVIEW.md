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

## Requested changes

| # | Feedback | What was wrong | Status |
|---|----------|----------------|--------|
| 1 | **Calibration must follow the model run**, not branch off data assembly. The paper's sequence is data prep → baseline model run → calibration → calibrated run. | `workflow_generator.py` had `calibrate` and `apply_params` consuming `assemble_rundir`'s tar, making them siblings of `run_nextgen` rather than its descendants. | **Implemented 2026-08-31**: both jobs now consume `rundir_{gage}_run.tar` from `run_nextgen`. Re-validation run pending (no cluster since the FABRIC maintenance). |
| 2 | **Evaluation must be hourly**, matching the paper's Fig. 10 metrics. | The model run and the calibration objective were already hourly, but the evaluation path was daily: `bin/fetch_usgs_obs.py` pulled NWIS *daily* values, and `bin/outputs_analysis.py` / `bin/teehr_evaluation.py` aggregated the hourly simulation to daily means before scoring. | **Implemented 2026-08-31**: obs are fetched hourly (instantaneous values resampled; daily-values fallback with a warning), and both evaluation jobs score hourly on timestamp-aligned joins, reporting the daily aggregate alongside (`resolution` column / `kge_daily`). Re-validation run pending. |
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

This file will be updated as the requested changes land.
