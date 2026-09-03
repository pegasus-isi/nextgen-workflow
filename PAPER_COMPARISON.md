# Results comparison: this workflow vs. Nassar et al. (2026)

**Status: COMPLETE (2026-08-03), re-validated on the corrected hourly path
(run0003, 2026-09-03).** The definitive comparison is now the
[run0003 section](#re-validation-on-the-corrected-hourly-path-run0003)
below — it is the first run where *every* number on both sides is hourly, so
it supersedes the daily-vs-hourly caveats that qualified run0006 and run0007.
Everything above that section is retained as the record of how the
reproduction got there.

Every published quantity is reproduced.
run0006 matched the uncalibrated-model and NWM v3.0 metrics within 0.01–0.05
KGE; its calibration leg exposed real defects in the vendored calibration
code (documented below, since fixed), and the re-run with fixes and parallel
multi-start DDS (run0007) **met and slightly exceeded the paper's calibrated
result: hourly KGE 0.898 vs the published 0.893**, with calibration now
improving the model (daily evaluation KGE 0.860 → 0.901).

| Calibrated result | Paper (Fig. 10) | run0007 |
|---|---|---|
| Calibration objective (hourly KGE) | 0.893 | **0.898** (trial seed 2 of 5; runner-up 0.871) |
| Evaluation period, daily KGE / NSE | — (hourly 0.893 / 0.785) | 0.901 / 0.804 |
| Effect of calibration | improves | improves (0.860 → 0.901 daily KGE) |

run0007 also demonstrated why multi-start matters: only 2 of 5 independent
200-iteration DDS trajectories beat the strong modern baseline; the three
that lost shipped `baseline_retained` instead of degrading the model. A
single sequential trajectory — the paper's method, and run0006's — is a coin
flip against today's pre-calibrated NGIAB defaults.

## Re-validation on the corrected hourly path (run0003)

Completed **2026-09-03**, 49/49 nodes Success, on the new
`pegasus-submit.pegasus.fabric` pool. This is the run that validates the two
code changes the author requested (see [`AUTHOR_REVIEW.md`](AUTHOR_REVIEW.md)):
calibration re-parented onto the baseline model run, and evaluation scored
**hourly on timestamp-aligned joins** instead of daily means. Both sides of
every row below are hourly.

| Quantity (evaluation period, WY2020–21, hourly) | Paper (Fig. 10) | run0003 | Δ |
|---|---|---|---|
| **Calibrated NextGen KGE** | **0.893** | **0.8903** | **−0.003** |
| Calibrated NextGen NSE | 0.785 | 0.7914 | +0.006 |
| NWM v3.0 KGE | 0.735 | 0.7361 | +0.001 |
| NWM v3.0 NSE | 0.752 | 0.7540 | +0.002 |
| Spin-up NWM v3.0 KGE / NSE | 0.418 / 0.428 | 0.4185 / 0.4288 | +0.001 |
| Spin-up uncalibrated NextGen KGE / NSE | 0.200 / −0.410 | 0.1752 / −0.4740 | −0.025 / −0.064 |

The uncalibrated defaults score **KGE 0.8598** on the evaluation period, so
calibration gained **+0.031 KGE**. Every published quantity lands within 0.003
except the uncalibrated spin-up NextGen row, which reproduces run0006's 0.1752
to four decimals — the same pre-existing difference in the uncalibrated model,
not something the author's fixes introduced.

Note which run each row comes from: the uncalibrated and NWM rows are read from
`gage-10109001_teehr_metrics.csv` (the default-parameter run) and the calibrated
row from `..._teehr_metrics_cal.csv`. Reading a spin-up number off the
*calibrated* file instead gives 0.1651 — that is the calibrated parameters
applied to the warm-up window, a different quantity from the paper's
uncalibrated spin-up, and not comparable to Fig. 10's 0.200.

**The calibrated re-run reproduced the calibration's own objective to seven
significant figures**: DDS reported `best_objective_value = 0.8903167830` for
the winning trial, and the independent calibrated evaluation scored
`0.8903167650`. That closes the loop run0006 left open — `apply_params` writes
the selected parameters into `realization.json` correctly, and the re-run
reproduces the score the sampler measured.

Multi-start again proved necessary. Five seeded 200-iteration DDS trajectories:

| Trial (seed) | Best iteration | Sampled KGE | Outcome |
|---|---|---|---|
| 1 | 156 | 0.8571 | `baseline_retained` (lost to 0.8598 defaults) |
| **2** | **190** | **0.8903** | **selected by the reducer** |
| 3 | 199 | 0.8739 | calibrated |
| 4 | 199 | 0.4547 | `baseline_retained` |
| 5 | 185 | 0.7776 | `baseline_retained` |

Three of five trajectories failed to beat the modern pre-calibrated NGIAB
defaults — the same pattern as run0007. A single sequential trajectory, which
is the paper's method, had a 40% chance of finding anything better than the
defaults on this basin.

**Reading trap.** `output/summary/summary_metrics.csv`'s headline `kge` column
is the **full 4-year period** (0.4365 default, 0.4216 calibrated), because it
spans the spin-up years. Taken at face value it suggests calibration made the
model worse. The paper-comparable numbers are the
`period=evaluation, resolution=hourly` rows of
`output/analysis/gage-10109001_teehr_metrics{,_cal}.csv`. Calibrated
full-period scoring slightly below default is expected: DDS optimizes the
evaluation window only, so spin-up drifts a little as a side effect.

**In-sample, by design.** `bin/lib/cal_utils.py` windows the observed series to
`>= training_start_date`, so the DDS objective covers 2019-10 → 2021-09 — the
same window labeled `period=evaluation`. The 0.8903 and the published 0.893 are
both in-sample calibration performance; the design has no held-out validation
window. See [`AUTHOR_REVIEW.md`](AUTHOR_REVIEW.md) for the full note.

Getting here took two attempts: the first pass reached 33/49 before one
calibration trial died twice on an MPICH nemesis TCP assertion inside
`ngen-parallel`. Root cause was a resource-request mismatch — `calibrate`
asked for 4 cores while PyNGIAB partitions across all 8 node cores and
launches one MPI rank per partition — fixed in `TOOL_CONFIGS` and resubmitted
from the rescue DAG with no change to the calibration code.

## What is being compared

| | Paper | This workflow (run0006) |
|---|---|---|
| Source | Nassar, A., et al. (2026). *A cloud-based JupyterHub platform for community research with the NextGen water resources modeling framework.* Env. Mod. & Software 203, 107031 ([PDF](references/nassar2026-envsoft-107031.pdf)); results from §3.2.3–3.2.4 and Fig. 10 | Pegasus run0006 on the pegasus2 HTCondor pool, completed 2026-08-01 (38/38 nodes) |
| Basin / gage | Logan River, UT — `gage-10109001` | same |
| Simulation period | 2017-10-01 → 2021-09-30 (4 water years) | same |
| Spin-up / calibration split | 2019-10-01 (paper §3.2.3; note the published notebooks say 2020-10-01 — the paper text was followed) | same (`--training-start 2019-10-01`) |
| Calibration | SPOTPY DDS, KGE objective, hourly, 200 iterations (~1.5 days on CIROH JupyterHub) | same (`--calibrate 200`, one sequential trajectory, ~19 h on one 4-core slot) |
| Model | NextGen: NOAH-OWP-Modular + CFE, t-route routing (NGIAB) | same engine image lineage (`awiciroh/ciroh-ngen-image:v1.9.0`) |
| Metric basis | **hourly** streamflow, via TEEHR | **daily** means vs NWIS daily values (pandas path; this gage is absent from TEEHR's warehouse crosswalk) — see caveats |
| NWM v3.0 source | TEEHR crosswalk + retrospective | USGS NLDI COMID (664424) + NOAA public retrospective zarr |

## Where the workflow reproduces the paper

Paper values are hourly, workflow values daily — yet three independent
quantities land within 0.01–0.05 of the published numbers, across four years
of upstream data drift (hydrofabric, AORC archive, `ngiab_data_preprocess`
4.9.1 vs. the paper's older version):

| Quantity | Paper (Fig. 10) | run0006 | Δ KGE |
|---|---|---|---|
| Uncalibrated NextGen, spin-up (WY2018–19) | KGE 0.200 / NSE −0.410 | KGE 0.175 / NSE −0.478 | −0.025 |
| NWM v3.0 retrospective, spin-up | KGE 0.418 / NSE 0.428 | KGE 0.409 / NSE 0.459 | −0.009 |
| NWM v3.0 retrospective, evaluation (WY2020–21) | KGE 0.735 / NSE 0.752 | KGE 0.727 / NSE 0.830 | −0.008 |

The uncalibrated-model agreement is the strongest single piece of evidence
that the notebook-to-workflow extraction preserved the science (SPEC §12
gate 4's purpose): same inputs pipeline, same engine, same statistics, arrived
at independently through completely different orchestration. The NWM
agreement additionally validates the workflow's independent NWM data path
(NLDI + NOAA zarr, not TEEHR's warehouse).

One number with no published counterpart: the workflow's **uncalibrated**
model on the evaluation period scores **KGE 0.860 / NSE 0.799** (daily) —
essentially at the paper's *calibrated* level. Today's default NGIAB
parameterization (newer preprocessor/hydrofabric) appears far better out of
the box than the 2025-era defaults the paper started from, which shrinks the
headroom calibration has to demonstrate.

## Where it diverges: calibration

| Quantity | Paper | run0006 |
|---|---|---|
| Calibrated NextGen, evaluation period | KGE **0.893** / NSE 0.785 (hourly) | KGE **0.612** / NSE 0.631 (daily) |
| Best calibrated hourly KGE reached by DDS | 0.893 | **0.610** (200 iterations, best at iter. 199) |
| Calibration effect | improved (0.418→0.893 vs NWM baseline framing) | **degraded** (uncalibrated 0.860 → calibrated 0.612, daily) |

## The calibration anomaly

*(Revised 2026-08-01 after an independent code review — an earlier version of
this section misread the objective column.)*

The iteration log's `objective_value` is **not KGE — it is KGE − 1**
(`cal_utils.py` subtracts 1 before handing the score to DDS; the raw `KGE`
column sits alongside it in the same CSV). Decoded, run0006's calibration is
internally *consistent*: DDS climbed from KGE −2.84 at initialization to
**0.610 hourly** at iteration 199, which matches the calibrated run's
independently computed 0.612 daily almost exactly. There is no
objective-vs-reality paradox.

The real finding is simpler and worse: **DDS never beat the default
parameterization (0.860), and the workflow installed its inferior winner
anyway.** The vendored calibration loop overwrites the model configs from the
very first sample and only ever selects among *sampled* vectors — the
untouched baseline is never evaluated as a candidate. The paper never noticed
because their era's defaults were poor (KGE 0.200): any decent DDS result was
an improvement. Today's defaults are excellent, so a 200-iteration random-init
DDS on 18 parameters plateaus below them, and the "best" calibrated
parameters are a downgrade.

Confirmed contributing defects (independent code review, ranked):
1. **Baseline never a candidate** (above) — directly explains the
   0.860 → 0.612 degradation. Fix: score the unmodified configuration first
   and apply sampled parameters only if they beat it.
2. **Positional obs/sim alignment with an unconditional off-by-one** —
   `evaluate()` truncates simulation by length (`[:len(obs)-1]`) while the
   evaluation array drops its first row (`[1:]`), pairing simulated hour T
   with observed hour T+1 *everywhere*, before any data gap (the record's one
   missing hour, 2019-10-28 20:00 UTC, shifts the tail further). Depresses
   the objective landscape for every candidate. Fix: timestamp inner-join.
3. **`update_parameters()` replaces the whole `model_params` dict** (may
   silently delete non-calibrated required parameters) and no-ops silently
   when no module name matches.
4. **Global-vs-per-catchment precedence still unverified** — calibration only
   edits the global formulation in `realization.json`; whether ngiab-4.9.1's
   per-catchment `config/cat_config/CFE/*.ini` files take precedence remains
   the open experimental question.

Also found in the same review (workflow wrappers, not exercised by run0006):
the multi-trial reducer would pick the *worst* trial under an RMSE objective
(DDS logs −RMSE internally); `apply_params` reports success based on payload
size rather than what was written; the calibrate wrapper refetches NWIS live
instead of using the staged observations (reproducibility risk); the SCE
branch never calls `sample()`.

**Diagnostic outcome (2026-08-01)** — the precedence experiment ran: extreme
CFE values applied through the global `realization.json` override changed the
outlet hydrograph drastically (mean flow 0.27 → 1.99 m³/s, max hourly
difference 124 m³/s). **Defect 4 is refuted: the global override reaches the
engine**, per-catchment ini files notwithstanding, and DDS was genuinely
steering the model. The experiment's config inspection also explained the
strong baseline: ngiab-4.9.1 realizations ship with `model_params` *already
populated* for CFE and NoahOWP — today's "defaults" are pre-calibrated
values, unlike the paper's 2025-era defaults (KGE 0.200). run0006's story is
therefore: a real but handicapped optimization (off-by-one objective
alignment; 200 iterations from random initialization against an
already-calibrated baseline) that plateaued at 0.610 and was installed only
because the baseline was never a candidate.

**Fixes applied (2026-08-01)**: timestamp inner-join alignment in the
objective (PEGASUS PATCH to `cal_utils.evaluate()`/`evaluation()`); baseline
scored first and retained whenever no sampled vector beats it
(`calibrate.py` + `apply_params.py` honor a `baseline_retained` payload);
`update_parameters()` merges instead of replacing and raises on module-name
mismatch; `best_objective_value` now carries the raw metric, fixing the
multi-trial reducer's direction handling for all objectives.

**Re-run outcome (run0007, 2026-08-03)**: paper configuration plus
`--dds-trials 5` — five independent 200-iteration DDS trajectories in
parallel, ~28 h wall clock on five worker slots. Per-trial best hourly KGE:
0.703, **0.898**, 0.871, 0.468, 0.659 (baseline 0.860 — three trials lost
and correctly retained the baseline in their payloads). The reducer selected
seed 2's parameters; the calibrated re-run scores **0.901 daily KGE / 0.804
NSE** on the evaluation period, up from the uncalibrated 0.860 / 0.799 and
at par with the paper's hourly 0.893 / 0.785. The calibration leg of the
comparison is closed: with a correctly aligned objective and the baseline as
a candidate, the workflow reproduces — marginally exceeds — the published
calibrated skill.

## Caveats on comparability

- ~~**Daily vs hourly**~~ **RESOLVED in run0003 (2026-09-03).** The caveat
  below applied to run0006/run0007, whose workflow metrics were daily-mean vs
  NWIS daily values while the paper's were hourly via TEEHR. Author feedback #2
  removed that mismatch: observations are now fetched hourly and both
  evaluation jobs score on timestamp-aligned hourly joins, reporting the daily
  aggregate alongside. The run0003 table above is hourly on both sides.
  Original caveat, for the earlier runs: for this baseflow-dominated karst
  basin the two bases track each other closely (the three matching quantities
  above bear that out), but they are not identical statistics.
- **Not bit-for-bit by design** (SPEC §10): the CONUS hydrofabric, AORC
  forcing archive, and NWM retrospective all drift; the preprocessor is
  pinned at 4.9.1 vs the paper's older version. "Close" is the achievable
  standard; the uncalibrated/NWM rows meet it.
- **Single DDS trajectory randomness**: a different seed explores differently;
  the paper reports one trajectory as well. This does not affect the anomaly
  analysis (a correct objective would not sit at −0.39 while true skill is
  0.6+).
- The paper's published notebooks disagree with the paper text on two
  parameters (training start 2020-10-01 vs 2019-10-01; repetitions 6 vs 200);
  the paper text was treated as authoritative. **The author confirmed the
  paper text on 2026-08-12** — of the 4-year period, the first two years
  initialize the model and the last two calibrate it, i.e. the 2+2 split that
  `--training-start 2019-10-01` produces.

## Artifacts

- Workflow outputs: `output.tgz` (run0006), key files:
  `output/analysis/gage-10109001_teehr_metrics{,_cal}.csv`,
  `output/calibration/gage-10109001_calibration_iterations.csv` (200 rows),
  `output/gage-10109001_best_params.json`.
- run0003 (the hourly re-validation) on `pegasus-submit.pegasus.fabric` at
  `~/nextgen-workflow/output/`: the same four files, plus a merged
  `calibration_iterations.csv` carrying all 1,000 iterations with a `trial`
  column (200 per seed). `best_params.json` records the winner as
  `seed=2, best_objective_value=0.8903167829912476,
  baseline_objective_value=0.8597975042424568`. The per-trial CSVs and
  payloads are intermediates and are removed by the cleanup job once the
  reducer consumes them — the merged log is the durable per-trial record.
- Paper: `references/nassar2026-envsoft-107031.pdf`, results in §3.2.3–3.2.4
  and Fig. 10.
