# Resume context — NextGen (NGIAB) workflow

Everything needed to pick this up cold. Written 2026-07-29, at the end of the
session that scaffolded the workflow.

**Read order for a fresh start**: this file → `README.md` (how to run it) →
`SPEC.md` (why it is shaped this way; §9–12 hold the constraints, non-goals,
expected outcome, and validation gates).

---

## Status in one line

Complete scaffold, 18/18 generator tests pass, valid DAG generated — **but the
container has never been built and nothing has ever run on a cluster**, so only
DAG generation is empirically validated.

## What exists

| Path | What it is |
|------|-----------|
| `workflow_generator.py` | 12 transformations, per-gage scatter, `8N+2` jobs (`12N+2` with `--calibrate N`) |
| `bin/*.py` | 12 wrapper scripts, one per transformation |
| `bin/lib/` | 4 utility modules **vendored from the paper** + `ngiab_pegasus.py` (new) |
| `tests/test_dag.py` | 18 generator tests — validation gate 1 |
| `run_manual.sh` | Step-by-step local run — validation gate 3 |
| `Apptainer/NextGen_Container.def` | `Bootstrap: docker` / `From: awiciroh/ciroh-ngen-image:v1.9.0` (`Docker/NextGen_Dockerfile` retained as a fallback) |
| `references/notebooks/` | The paper's 5 original notebooks + HydroShare README |
| `references/original_utils/` | **Unpatched** copies of the vendored modules |
| `SPEC.md` | The conversion plan (moved here from `../agu/`) |
| `config/gages.txt` | Gage list; currently just the paper's demo basin |

Provenance: paper is Nassar et al. 2026, *Environmental Modelling & Software* 203,
107031, https://doi.org/10.1016/j.envsoft.2026.107031. Notebooks:
https://doi.org/10.4211/hs.27045581bdea4808a393330f2417379c.

**MILESTONE 2026-08-12: AUTHOR REVIEW (Ayman Nassar) — reproduction confirmed,
two code fixes requested.** Review meeting with the paper's author (recorded in
`AUTHOR_REVIEW.md`, checked in; raw Zoom summary in `MeetingAssets.pdf`,
untracked). Verdict: the small calibration deviations
(he cited 0.735 vs 0.727) are *expected* — DDS is stochastic, and our parallel
seeded trials differ from the paper's single sequential trajectory — so the
"reproduced within noise" claim stands. His requested changes:

1. **Re-parent calibration onto the model run.** Today `calibrate` (and
   `apply_params`) consume `rundir_tar` from `assemble_rundir`, making them
   siblings of `run_nextgen`. The paper's sequence is prep → baseline run →
   calibrate → calibrated run; feed them `rundir_{gage}_run.tar` instead.
2. **Evaluate hourly, not daily.** The model already runs hourly and the
   calibration objective is already hourly, but the evaluation path is daily:
   `fetch_usgs_obs.py` pulls NWIS daily values and `outputs_analysis.py` /
   `teehr_evaluation.py` aggregate sim to daily means. Paper Fig. 10 metrics
   are hourly — fetch hourly/instantaneous obs and score hourly (or both).
3. **Workflow diagram** should be restructured to show data prep → model
   setup/run → calibration in sequence (follows from fix 1); Ayman offered to
   help with the flowchart.

Other action items: share deck + code with Ayman via a Google folder
(Komal/Ewa); Ewa schedules follow-up with David + Ayman. Ayman confirmed the
4-year window is 2 yr initialization + 2 yr calibration (matches
`--training-start`). Feeds the AGU talk and a planned paper on AI methods for
hydrological workflows. NOTE: the meeting's 0.735/0.727 numbers differ from
run0007's 0.898-vs-0.893 — 0.735 is the paper's *NWM v3.0* KGE (Fig. 10), so
he was likely reading the NWM row on a slide; double-check which numbers the
deck shows before re-sharing.

**MILESTONE 2026-08-03: PAPER FULLY REPRODUCED (run0007).** Fixed multi-start
calibration (5×200 DDS trials) reached hourly KGE 0.898 vs the paper's 0.893;
calibrated daily eval KGE 0.901/NSE 0.804 (up from baseline 0.860/0.799).
Only 2 of 5 trials beat the baseline — the losing trials retained defaults
via `baseline_retained`. Comparison closed in `PAPER_COMPARISON.md`.

**MILESTONE 2026-08-01: paper-matching run (run0006) COMPLETE — see
`PAPER_COMPARISON.md`.** Uncalibrated NextGen and NWM v3.0 metrics reproduce
the published Fig. 10 values within 0.01–0.05 KGE (extraction validated).
Calibration diverges: DDS optimized a broken objective (peak −0.39 hourly KGE
while true daily skill is 0.6+) and degraded the model (0.860 → 0.612);
suspects are cal_utils' length-based obs/sim alignment and the global
realization.json param override not reaching per-catchment CFE inis in
ngiab-4.9.1 configs. Diagnostic plan documented. Also: today's defaults score
KGE 0.860 uncalibrated on WY20-21 — near the paper's calibrated 0.893.

**MILESTONE 2026-07-30: first full cluster run SUCCEEDED** — 32/32 nodes, gage
10109001, 2017-10-01..2021-09-30, on the pegasus2 condorpool. Outputs under
`pegasus2:/home/ubuntu/nextgen-workflow/output/` (hydrograph/water-balance/teehr
plots, metrics CSVs; KGE 0.44 / NSE 0.03 vs USGS daily obs, method
`pandas_fallback` as designed). Fixes that got it there: dnf not apt; uv not
pip; install-only-missing pins; no TEEHR; `/opt/preprocess` venv for
`ngiab_data_cli` + piped "y" for its interactive prompt; pyngiab installed and
PEGASUS-PATCHed; 14 GB (not 16) memory requests. Next: validation gate 4,
notebook parity.

## The next thing to do

Everything through the paper-matching run is DONE (build, gate 3, plan/submit,
run0005 clean 38/38, run0006 comparison — `PAPER_COMPARISON.md`). What remains:

0. **Author-review fixes (2026-08-12, see milestone above)** — code DONE
   2026-08-31 (calibrate/apply_params re-parented onto `run_nextgen`'s output
   tar; obs fetched hourly with daily fallback; both evaluation jobs score
   hourly on timestamp joins with daily aggregates alongside; README diagram
   updated; status table in `AUTHOR_REVIEW.md`). Remaining: slide-deck
   figures, and a re-validation run once a cluster exists again.
1. **Calibration-objective diagnostic** (the open investigation; plan in
   `PAPER_COMPARISON.md`): (a) one default-parameter 4-year run, hourly KGE
   computed length-aligned vs timestamp-aligned; (b) one run with extreme CFE
   values via the global `realization.json` override to test whether it
   reaches per-catchment `config/cat_config/CFE/*.ini` in ngiab-4.9.1
   configs. Then fix `cal_utils` (PEGASUS PATCH) or the wrapper accordingly.
2. **Formal gate 4** (optional now — the run0006 comparison substantially
   covers it): execute notebooks 2–3 once, diff basin-mean/metrics CSVs.
3. Multi-gage production runs; `--dds-trials N` for parallel calibration.

## Five known gaps (expect the first run to hit some)

Ordered by how likely they are to bite. Also in `README.md`.

1. **`fetch_hydrofabric` cache location is an assumption.** It warms the cache by
   requesting a subset, then tars `$HOME/.ngiab`. If this image version caches
   elsewhere, the job fails with a message saying where it looked.
2. **`generate_realization` runs parallel to `generate_forcings`.** In the notebook
   `-r` takes only the date range, so they are siblings. If a real run shows `-r`
   needs `forcings/forcings.nc`, pass `--forcings-tar` and add `forcings_tar` to
   that job's inputs — the wrapper already accepts it and the generator has a
   comment marking the exact spot. One-line change.
3. ~~The TEEHR path is not wired up~~ **Wired and validated 2026-07-30.**
   `teehr_evaluation` runs real TEEHR in `kthare10/nextgen-teehr:x86`
   (second entry in the transformation catalog, `--teehr-image` to override) —
   a thin derivative of CIROH's `awiciroh/ngiab-teehr:x86` adding curl/wget,
   which PegasusLite needs in-container (`Apptainer/Teehr_Container.def`; found
   2026-07-31 when the first Pegasus teehr job failed to bootstrap its worker
   package). TEEHR still cannot live in the engine venv. **2026-07-31 late:**
   the pandas fallback now ALSO fetches the NWM v3.0 benchmark directly
   (NLDI gage→COMID, then NOAA's public retrospective zarr via s3fs/xarray) —
   validated for demo gage 10109001 (reach 664424); hydrofabric `hf_id` is
   NOT an NWM feature id, NLDI is authoritative. Every gage now gets the NWM
   comparison one way or the other.
   The wrapper drives the image's bundled `/app/teehr_ngen.py` and maps its
   `teehr/metrics.csv` (NextGen AND NWM v3.0 retrospective vs USGS) into our
   schema, `method="teehr"`. Validated by running the wrapper inside the
   container against a real run dir (gage-06910750). Gages missing from
   TEEHR's USGS↔NWM crosswalk (6,903 of 17,637 hydrofabric gages are present;
   demo gage 10109001 is NOT — its NWM twin is 10109000) fall back to pandas,
   recorded in `method`. Not yet exercised through a full Pegasus run.
4. ~~Container package pins are best-effort~~ **Resolved 2026-07-29**: the layer
   now installs only the four packages missing from the v1.9.0 venv
   (dataretrieval, spotpy, s3fs pinned to the venv's fsspec, rioxarray), verified
   by dry-run to change nothing pre-installed. Base image is Rocky Linux (dnf,
   not apt) and its venv has no pip — installs go through the image's own `uv`.
   The base image also does NOT ship `ngiab_data_cli` (isolated venv at
   `/opt/preprocess`, found via `NGIAB_CLI_PYTHON`) nor `pyngiab` (installed
   from github.com/fbaig/ciroh_pyngiab pinned to f17e6fc, **with a PEGASUS
   PATCH**: upstream `_check_dependencies` returns False when dependencies are
   valid on the first probe — only the venv-retry branch can return True — so
   `Apptainer/NextGen_Container.def`'s `%post` rewrites the fall-through
   `return False` to `return valid_env`; see the comment there).
5. ~~Calibration untested~~ **Validated offline 2026-07-31** (calibrate →
   apply_params chain in the container: 6 DDS reps, per-module best_params
   JSON, 11 CFE + 7 NoahOWP params applied to realization.json, calibrated
   re-run tar produced; iteration log was exactly where the wrapper guessed).
   Now ON by default (6 reps — DDS needs > ~5 init samples; `--calibrate 0`
   disables; user decision 2026-07-31). Fixes: hourly m³/s obs pickle built
   via the paper's `process_usgs_streamflow()` (NOT the staged daily CSV);
   algorithm/objective uppercased for run_spotpy's comparisons; best_params
   converted from SPOTPY's positional vector to write_config's per-module
   config-key dicts; apply_params targets realization.json (not ini globs).
   Remaining: a clean pass inside a full Pegasus run — run0003's staged
   wrappers are stale, so submit a FRESH plan rather than rescuing it.

## Decisions worth not re-litigating

- **Vendored the paper's own utility modules** rather than reimplementing the
  analysis and calibration math. `references/original_utils/` holds the unpatched
  copies; `diff` against `bin/lib/` shows the only two changes, both marked
  `PEGASUS PATCH`: the hardcoded `/home/jovyan/ngiab_preprocess_output` root now
  reads `NGIAB_DATA_ROOT`, and a `/home/jovyan` fallback CWD became `/tmp`.
  **Re-apply both if these modules are ever re-downloaded from HydroShare.**
- **Run directories travel as tarballs**, one per stage (`rundir` → `rundir_run` →
  `rundir_cal`). The notebooks mutate a tree in place and edit `realization.json`
  repeatedly; this keeps strict file-based dataflow and archives each stage.
- **`assemble_rundir` exists because the prep branches emit loose subtrees.**
  Without it `run_nextgen` declares an input no job produces. This was a real
  defect caught in spec review, which is why there is now an automated test for
  the whole class of bug.
- **No hidden state**: the routing `feature_id` the notebooks hardcoded after an
  interactive query is derived at run time from the hydrofabric.
- **CLI wrappers, not papermill**: shell magics, hand-copied cell values, and
  interactive Folium/hvplot cells make notebook execution brittle in batch.
- **Plot failures degrade to a placeholder image** instead of failing the job, so a
  cosmetic problem never kills a completed model run.
- **Calibration off by default** — each DDS iteration is a full ~5–7 min model run.

## Testing notes

```sh
source .venv/bin/activate && python -m pytest tests/ -v     # 18 tests, ~2s
```

Two of these tests are worth preserving deliberately, because they catch bugs that
otherwise only appear at plan time or on a worker node:

- `test_no_dangling_inputs` — every job input is some job's output or a Replica
  Catalog entry. **This one was mutation-tested**: deleting the `assemble_rundir`
  job makes it fail with exactly the original defect. If you refactor the
  generator, re-run that mutation to confirm the test still has teeth.
- `test_argparse_contract` — cross-checks every flag the generator passes against
  each wrapper's `add_argument` calls, in both directions.

## Note on Claude Code memory

Project memory is keyed to the working directory. The notes from the session that
built this live under the **`workflows/agu`** project, so starting Claude Code in
this directory will not load them — which is why this file exists and is checked in
alongside the code. The sibling specs for the two unbuilt workflows are still in
`../agu/`, and `../../cc-usage-log.md` has the full session history.
