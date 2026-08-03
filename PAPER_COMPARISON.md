# Results comparison: this workflow vs. Nassar et al. (2026)

**Status: COMPLETE (2026-08-03).** Every published quantity is reproduced.
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

- **Daily vs hourly**: workflow metrics are daily-mean vs NWIS daily values;
  the paper's are hourly via TEEHR. For this baseflow-dominated karst basin
  the bases track each other closely (the three matching quantities above
  bear that out), but they are not identical statistics.
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
  the paper text was treated as authoritative.

## Artifacts

- Workflow outputs: `output.tgz` (run0006), key files:
  `output/analysis/gage-10109001_teehr_metrics{,_cal}.csv`,
  `output/calibration/gage-10109001_calibration_iterations.csv` (200 rows),
  `output/gage-10109001_best_params.json`.
- Paper: `references/nassar2026-envsoft-107031.pdf`, results in §3.2.3–3.2.4
  and Fig. 10.
