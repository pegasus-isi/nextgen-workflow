# Spec: NextGen (NGIAB) Watershed Modeling — Pegasus Workflow Conversion

**Status**: IMPLEMENTED AND VALIDATED — first full cluster run 2026-07-30
(32/32); first 100%-clean DAG with calibration + TEEHR 2026-07-31 (run0005,
38/38); paper-matching run complete 2026-08-01 (run0006, 200 DDS iterations —
results vs the publication in `PAPER_COMPARISON.md`: uncalibrated and NWM
metrics reproduce Fig. 10 within 0.01–0.05 KGE, substantially satisfying
gate 4). Sections 1–12 are the plan as reviewed; **§13 records where the
as-built implementation deviates from it**. Open: the vendored calibration
objective misbehaves under current NGIAB configs (§13.11).
**Source paper**: Nassar, A., et al. 2026, "A cloud-based JupyterHub platform for community
research with the NextGen water resources modeling framework," *Environmental Modelling &
Software* 203, 107031. https://doi.org/10.1016/j.envsoft.2026.107031
**Source code**: HydroShare resource
https://doi.org/10.4211/hs.27045581bdea4808a393330f2417379c (5 Jupyter notebooks + 5 Python
utility modules, ~250 KB)

**Scope and acceptance criteria**: §9 constraints, §10 non-goals, §11 expected outcome,
§12 validation gates.

---

## 1. What the pipeline does

Runs the **NOAA NextGen Water Resources Modeling Framework** ("NextGen In A Box" / NGIAB)
for any research-scale watershed in CONUS: subset the national hydrofabric at a USGS gage,
generate AORC meteorological forcings, run NextGen (NOAH-OWP-Modular + CFE land models,
t-route channel routing), evaluate simulated streamflow against USGS observations and NWM
v3.0 retrospective (via TEEHR), and calibrate model parameters with SPOTPY/DDS.

The paper's premise — running many independent watershed subdomains — makes this an ideal
Pegasus scatter workflow: the entire chain is embarrassingly parallel per gage.

## 2. Source pipeline (notebook order)

| # | Notebook | What it does | Runtime (1 gage, 4 yr) |
|---|----------|--------------|------------------------|
| 1 | `NextGen_Data_Preparation.ipynb` | Download/subset CONUS hydrofabric (`ngiab_data_cli -s`), subset+regrid AORC forcings (`-f`), generate `realization.json` + module configs (`-r`), patch in 27 output variables, forcing QC plots | ~5 min (+2–3 min hydrofabric cache on first run) |
| 2 | `NextGen_Run.ipynb` | `PyNGIAB(...).run()` — NextGen + t-route; gage↔segment crosswalk; fetch USGS obs (NWIS); hydrograph plot | ~10 min |
| 3 | `NextGen_Outputs_Analysis.ipynb` | Area-weighted basin aggregation of NOAH/CFE/routing outputs; snow/canopy diagnostics; RMSE/KGE vs USGS; water-balance plots | minutes |
| 4 | `NextGEN_TEEHR_Evaluation.ipynb` | TEEHR (Spark) evaluation: USGS + NWM v3.0 retrospective from public S3, NLDI COMID lookup, KGE/NSE for spin-up vs calibration periods | minutes |
| 5 | `NextGen_Calibration.ipynb` | SPOTPY DDS (KGE objective): each iteration rewrites CFE (11 params) / NoahOWP (7 params) configs and re-runs NextGen (~5–7 min/iter) | hours (N iterations) |

Key parameters repeated across notebooks (become workflow arguments):
`hydrofabric_id` (e.g., `gage-10109001`), `start_date`, `end_date`, downstream
`feature_id`, `training_start_date`.

## 3. Proposed Pegasus DAG

One sub-chain per gage; scatter over a gage list. Analysis and TEEHR evaluation are
independent siblings after the model run.

```
                     ┌──────────────────────┐
                     │ subset_hydrofabric    │  (per gage)
                     └──────┬───────────────┘
                ┌───────────┴───────────┐
                ▼                       ▼
      ┌──────────────────┐   ┌──────────────────────┐
      │ generate_forcings │   │ generate_realization │
      └─────────┬────────┘   └──────────┬───────────┘
                └───────────┬───────────┘
                            ▼
                  ┌──────────────────────┐
                  │ assemble_rundir       │  fan-in → rundir_{gage}.tar
                  └──────────┬───────────┘
                             ▼
                  ┌──────────────────┐        ┌────────────────┐
                  │ run_nextgen       │◄──────│ fetch_usgs_obs │ (NWIS)
                  └───┬──────────┬───┘        └───────┬────────┘
                      ▼          ▼                    │
        ┌────────────────┐  ┌────────────────┐        │
        │ outputs_analysis│  │ teehr_evaluation│◄──────┘
        └────────┬───────┘  └────────┬───────┘
                 └──────────┬────────┘
                            ▼   (optional branch)
                  ┌──────────────────┐
                  │ calibrate (DDS)   │  → best_params.json
                  └────────┬─────────┘
                           ▼
                  ┌──────────────────┐
                  │ rerun + post-eval │  (unrolled calibration cycle)
                  └────────┬─────────┘
                           ▼
                  ┌──────────────────┐
                  │ summarize (merge  │  (across all gages)
                  │  metrics, plots)  │
                  └──────────────────┘
```

### Jobs / transformations

| Job | Wraps | Inputs | Outputs |
|-----|-------|--------|---------|
| `subset_hydrofabric` | `ngiab_data_cli -s` | gage ID; CONUS hydrofabric (pre-staged, see §5) | `{gage}_subset.gpkg` |
| `generate_forcings` | `ngiab_data_cli -f` | subset gpkg, date range | `forcings.nc`, `raw_gridded_data.nc`, QC PNGs |
| `generate_realization` | `ngiab_data_cli -r` + realization patcher (from notebook 1) | subset gpkg | `realization.json`, module configs (tarred) |
| `fetch_usgs_obs` | `dataretrieval` NWIS pull (extracted from notebooks 2/3) | gage ID, date range | `usgs_obs.csv` |
| `assemble_rundir` | small `tar` wrapper (new; no notebook equivalent) | `{gage}_subset.gpkg`, `forcings.nc`, `raw_gridded_data.nc`, `realization.json`, module-config tarball | `rundir_{gage}.tar` laid out as NGIAB expects (`config/`, `forcings/`, `outputs/`) |
| `run_nextgen` | `PyNGIAB(...).run()` wrapper | `rundir_{gage}.tar` | `rundir_{gage}_run.tar` (cat-* CSVs, `troute/*.nc`), crosswalk, hydrograph PNG |
| `outputs_analysis` | `ngen_output_analysis()` from `ngen_outputs_utils.py` | `rundir_{gage}_run.tar`, `usgs_obs.csv` | basin-mean CSV, metrics CSV, water-balance PNGs |
| `teehr_evaluation` | TEEHR script (extracted from notebook 4) | troute NetCDF, gage ID | KGE/NSE parquet/CSV, evaluation PNG |
| `calibrate` | `run_spotpy` DDS wrapper | `rundir_{gage}.tar` (pre-run tree — calibration re-runs the model itself), `usgs_obs.csv`, n_iterations | `calibration_iterations.csv`, `best_params.json` |
| `apply_params_rerun` | param patcher + `PyNGIAB` | `rundir_{gage}.tar`, `best_params.json` | `rundir_{gage}_cal.tar` |
| `post_cal_eval` | reuse `outputs_analysis`/`teehr_evaluation` | calibrated run outputs | post-cal metrics |
| `summarize` | merge script | all per-gage metrics | combined metrics CSV, summary plots |

### Conversion strategy: scripts, not papermill

Extract the computational cells into plain Python CLI scripts under `bin/` (the notebooks
already delegate most logic to the 5 utility modules, so this is mostly writing thin CLI
entrypoints). Rationale: shell magics (`!source /ngen/.venv/bin/activate && ...`), hidden
state (values hand-copied between cells, e.g., a hardcoded `nwm_feature_id` after an API
query cell), and Folium/hvplot interactive cells make papermill conversion brittle.
Interactive visuals are dropped or replaced with PNG output.

## 4. Data sources and credentials

| Source | Access | Credentials | Size |
|--------|--------|-------------|------|
| CONUS hydrofabric (Lynker/NOAA) | anonymous download via `ngiab_data_preprocess` | none | several GB (cache once) |
| AORC forcings | public cloud via `ngiab_data_cli` | none | MBs–low GBs per basin |
| USGS NWIS (obs streamflow) | `dataretrieval` REST | none | small |
| USGS NLDI (COMID lookup) | REST | none | tiny |
| NWM v3.0 retrospective + TEEHR crosswalks | anonymous S3 (`s3a://ciroh-rti-public-data`) | none | small subsets |

**No credentials required anywhere** — a major simplification vs. our other workflows.
All network jobs still get retry/backoff + `add_dagman_profile(retry="2")`, and all
sources here are REQUIRED (fail-loud: write declared empty output, then exit non-zero).

## 5. Container and staging plan

- **Base image**: `awiciroh/ciroh-ngen-image` (existing NGIAB Docker image) extended with
  `teehr` (+ Java for Spark), `spotpy`, `dataretrieval`, `geopandas`, plotting deps →
  publish as `kthare10/nextgen-workflow`. NextGen + `ngiab_data_cli` live in `/ngen/.venv`
  inside the image; wrapper scripts invoke that venv explicitly.
- **CONUS hydrofabric**: several-GB download that must NOT happen per job. Options:
  (a) pre-bake into the container image, (b) stage once as a Pegasus input replica and
  share, or (c) one `fetch_hydrofabric` job whose output feeds all `subset_hydrofabric`
  jobs. **Recommend (c)** — keeps the image small and the file cacheable by Pegasus.
- **Run-directory problem**: the notebooks communicate via a mutable directory tree
  (`ngiab_preprocess_output/{gage}/`), and `realization.json` is edited in place by both
  prep and calibration. Plan: **tar the run dir between jobs** (declared as a single
  Pegasus file per edge), untar/retar inside each wrapper. Keeps strict file-based data
  flow and provenance. Because the two prep branches emit loose files rather than a tree,
  an explicit `assemble_rundir` fan-in job builds the first tarball — every later job
  consumes exactly one tarball and emits a new one (`rundir` → `rundir_run` →
  `rundir_cal`), so no job depends on a tree no upstream job produced, and each stage's
  tree is separately archived instead of mutated in place.
- Hardcoded `/home/jovyan` paths → parameterized `--data-dir` in every wrapper.

## 6. Parallelization

- **Across gages**: entire chain scatters per `hydrofabric_id` — the primary axis; the
  generator takes a gage list file.
- **Within prep**: forcings and realization generation run as parallel siblings after the
  hydrofabric subset.
- **Analysis vs TEEHR**: parallel siblings after `run_nextgen`.
- **Within NextGen**: `PyNGIAB(serial_execution_mode=False)` multiprocesses across
  catchments inside one job — request multiple cores via Pegasus profiles.
- **Calibration**: DDS is sequential per trial; parallelize by fanning out multiple DDS
  trials/seeds as sibling jobs and picking the best (optional `--dds-trials N`).

## 7. Risks / open questions

1. **Calibration cost**: each DDS iteration is a full ~5–7 min model run. Default the
   workflow to `--skip-calibration`, with iteration count as an argument when enabled.
2. **TEEHR/Spark in a job**: local Spark session needs memory + Java in the container and
   working anonymous `s3a` access from worker nodes. Fallback: fetch NWM retrospective via
   plain HTTPS/zarr and compute KGE/NSE with pandas if TEEHR proves too heavy.
3. **Run-dir tarball size**: forcings + outputs for large basins/long periods could reach
   GBs — acceptable for Pegasus staging but worth documenting; troute NetCDF alone may
   suffice for the evaluation branch.
4. **Calibration cycle unrolled** as calibrate → apply-params → rerun → post-eval (one
   unroll, matching the paper's usage); iterative recalibration is out of scope.
5. Notebook filename casing quirk (`NextGEN_TEEHR_Evaluation.ipynb`) — irrelevant after
   script extraction, noted for provenance.

## 8. Deliverable layout (matches repo conventions)

```
nextgen-workflow/
├── workflow_generator.py      # scatter over --gages list; flags: --start/--end dates,
│                              #   --calibrate N, --dds-trials
├── bin/
│   ├── fetch_hydrofabric.sh
│   ├── subset_hydrofabric.sh
│   ├── generate_forcings.sh
│   ├── generate_realization.py
│   ├── assemble_rundir.sh
│   ├── fetch_usgs_obs.py
│   ├── run_nextgen.py
│   ├── outputs_analysis.py
│   ├── teehr_evaluation.py
│   ├── calibrate.py
│   ├── apply_params.py
│   └── summarize.py
├── Docker/Dockerfile          # FROM awiciroh/ciroh-ngen-image
├── requirements.txt
├── README.md
└── example_usage.sh
```

**Estimated effort**: ~2–3 days (container + wrappers + generator + test on 1–2 gages).

---

## 9. Constraints

Hard requirements the implementation must satisfy.

- **Platform**: Pegasus 5.x + HTCondor on the existing submit infrastructure; all compute
  jobs containerized.
- **Container lineage**: built `FROM awiciroh/ciroh-ngen-image`; the NextGen engine and
  `ngiab_data_cli` are invoked from the image's `/ngen/.venv`, not a rebuilt environment.
  Pin `ngiab_data_preprocess`, `pyngiab`, and `teehr` versions in the image.
- **Zero credentials.** Every data source is public/anonymous. If any step turns out to
  need auth, treat that as a design error and revisit, don't paper over it with a secret.
- **CONUS hydrofabric fetched at most once per workflow run** and shared by all gage
  branches — never once per gage (it is several GB).
- **All inter-job state travels as declared Pegasus files** (run-dir tarballs). No reliance
  on a shared filesystem, and `realization.json` is never edited in place across a job
  boundary: each stage emits a new tarball (`rundir` → `rundir_run` → `rundir_cal`).
- **Determinism**: gage list, date range, and every parameter come from explicit CLI
  arguments. No wall-clock defaults, no values hand-copied from a prior step (the notebooks'
  hardcoded `nwm_feature_id` must be derived programmatically or passed in).
- **Network jobs**: retry with backoff plus `add_dagman_profile(retry="2")`. All sources are
  REQUIRED, so on final failure write the declared (empty) output *then* exit non-zero —
  exiting without the declared file makes HTCondor HOLD the job and hang the DAG.
- **Resource declarations per transformation**: the TEEHR job needs Java and several GB of
  RAM for its local Spark session; don't apply one blanket memory request to every job.
- **Calibration off by default** (`--calibrate N` opt-in): each DDS iteration is a full
  ~5–7 min model run.
- **Repo conventions**: `workflow_generator.py` + `bin/` + `Docker/` + `README.md` +
  `example_usage.sh`; image published under the `kthare10` registry.

## 10. Non-constraints (explicit non-goals)

Stating these so nobody spends time on them or treats their absence as a defect.

- **Not** bit-for-bit or metric-for-metric reproduction of the paper's numbers. The CONUS
  hydrofabric, AORC forcings, and NWM v3.0 retrospective all drift; matching published KGE
  exactly is not a goal (see §11 for what we do assert).
- **Not** a port of the interactive platform. No JupyterHub, no 2i2c hub dependency, no
  Folium/hvplot/bokeh interactivity, no widgets — visual outputs are static PNGs.
- **Not** required to keep the notebooks runnable or to execute them via papermill; the
  notebooks are the specification, the CLI wrappers are the artifact.
- **No GPU** anywhere.
- **Not** required to hit any particular calibrated KGE, or to calibrate at all by default.
- **Not** required to support domains outside CONUS (the hydrofabric is CONUS-only).
- **Not** required to publish results anywhere — no HydroShare or S3 upload.
- **Not** an operational/real-time forecasting system: retrospective runs only.
- **Not** required to handle continental-scale single domains or multi-decade periods in v1.

## 11. Expected outcome

- `python workflow_generator.py --gages gages.txt --start 2017-10-01 --end 2021-09-30`
  produces a plannable workflow YAML.
- **Job count**: `8N + 2` for N gages without calibration (2 shared jobs —
  `fetch_hydrofabric`, `summarize` — plus 8 per gage); roughly `12N + 2` with the
  calibration branch enabled.
- **Runtime** for the paper's demo gage (`gage-10109001`, WY2018–2021): `run_nextgen`
  ~10 min, whole single-gage DAG ~20–30 min wall clock. N gages run concurrently, so
  wall clock stays near the single-gage time until the concurrency cap binds.
- **Artifacts per gage**: run-dir tarballs, basin-mean aggregation CSV, metrics CSV
  (RMSE/KGE vs USGS), TEEHR KGE/NSE table for spin-up vs evaluation periods, hydrograph
  and water-balance PNGs. Plus one combined cross-gage metrics CSV and summary plots.
- **Scientific expectation** (plausibility, not equality): the simulated hydrograph tracks
  USGS observations with a positive KGE, seasonal snow accumulation and melt appear in the
  NOAH/CFE diagnostics, and the TEEHR comparison places NextGen alongside NWM v3.0 for the
  same period.

## 12. Validation

Ordered gates — each is cheap relative to the one after it, so failures surface early.
**Build gate 4 first**; it is the strongest correctness anchor and it constrains everything
upstream of it.

1. **Generator unit tests** (no execution): assert DAG shape for N=1 and N=3 gages matches
   the `8N + 2` formula, and — critically — that **every job input is either a staged input
   or some other job's declared output**. A missing-producer check is exactly the bug that
   made `assemble_rundir` necessary, so it belongs in CI rather than in a reviewer's head.
2. **Container smoke test**: `ngiab_data_cli --help` from `/ngen/.venv`;
   `python -c "import pyngiab, teehr, spotpy, dataretrieval"`; `java -version` (Spark);
   confirm pinned versions match the image manifest.
3. **Short single-gage slice** (3-month window, paper's gage): `forcings.nc` time dimension
   equals the number of hours in the window; `realization.json` parses and carries all 27
   `output_variables`; the run emits a non-empty `troute` NetCDF and per-catchment CSVs.
4. **Notebook parity** (the anchor): run notebooks 2–3 interactively once in the container
   for the same gage and period, then diff the batch pipeline's basin-mean and metrics CSVs
   against the notebook's to within float tolerance. This is what proves the CLI extraction
   didn't silently change the science.
5. **Internal metric agreement**: KGE computed by two independent paths — pandas in
   `outputs_analysis` and Spark in `teehr_evaluation` — agrees to ~1e-3. Disagreement means
   a crosswalk or unit-conversion bug, the most likely silent failure here.
6. **Physical plausibility**: water-balance closure (precip ≈ runoff + ET + storage change)
   within a few percent; SWE non-negative and melting out each summer; no negative flows.
7. **Scatter isolation**: 3-gage run produces distinct per-gage outputs with no cross-talk
   (compare hydrographs; identical outputs across different gages means a path collision).
8. **Rerun/idempotency**: force `run_nextgen` to fail, then resume — the workflow must not
   re-fetch the multi-GB hydrofabric or redo completed prep jobs.
9. **Secret scan**: grep the generated YAML, submit files, and kickstart records for
   credential-shaped strings. This workflow should have none; the check exists so the habit
   is in place for CWARHM, where it matters.

## 13. As-built deviations from this spec (2026-07-30)

Everything below was discovered while getting the first cluster run to succeed.
The spec text above is unchanged; where they disagree, this section is current.

1. **TEEHR is not in the image — it runs in a second container** (deviates from
   §5 and §12 gate 2). Every release (0.4.6–0.6.6) forces pyarrow/pydantic/zarr
   downgrades that would corrupt `/ngen/.venv`, and a `pip install ... || true`
   guard cannot catch that failure mode because the install *succeeds* while
   doing the damage. Instead, the `teehr_evaluation` transformation runs in
   `kthare10/nextgen-teehr:x86` — a thin derivative (`Docker/Teehr_Dockerfile`)
   of CIROH's companion image `awiciroh/ngiab-teehr:x86` (the split NGIAB
   itself uses) that adds curl/wget for PegasusLite's in-container worker
   package download; the wrapper drives its bundled `teehr_ngen.py` and maps the result
   into the workflow schema. Validated 2026-07-30 against a real run dir:
   NextGen + NWM v3.0 retrospective vs USGS in one metrics table. Gages absent
   from TEEHR's USGS↔NWM crosswalk (6,903 of 17,637 hydrofabric gages are
   present; the demo gage 10109001 is NOT — its NWM twin is 10109000) take §7
   risk 2's pandas fallback, recorded in the `method` column.
2. **`ngiab_data_cli` is not in the base image** (deviates from §5/§9, which
   assumed it lives in `/ngen/.venv`). It comes from the separate
   `ngiab_data_preprocess` package (pinned 4.9.1), whose pins also conflict with
   the engine venv — it gets its own venv at `/opt/preprocess`, and wrappers
   locate it via the `NGIAB_CLI_PYTHON` env var (falls back to `NGIAB_PYTHON`
   outside the image). Its cold-cache hydrofabric prompt has no non-interactive
   bypass, so `run_ngiab_cli()` pipes `y` answers to stdin (under Pegasus, stdin
   is `/dev/null` and `rich.Prompt` dies with EOFError otherwise).
3. **`pyngiab` is not in the base image either** and carries a **PEGASUS PATCH**
   for an upstream bug at the pinned commit (`f17e6fc`, github.com/fbaig/
   ciroh_pyngiab): `_check_dependencies` can only return True through its
   venv-retry branch, so an environment whose first probe already has correct
   pydantic/numpy is rejected. The paper's JupyterHub masked this (wrong system
   python, correct venv). The Dockerfile rewrites the fall-through
   `return False` to `return valid_env`, asserts the patch target still exists,
   and constructs `PyNGIAB` at build time as a smoke test.
4. **Base image practicalities** (§5): `awiciroh/ciroh-ngen-image:v1.9.0` is
   Rocky Linux — `dnf` (with `--allowerasing` for the `curl-minimal` conflict),
   not `apt` — and its venv ships without pip; all installs go through the
   image's bundled `uv`. Only packages absent from the venv are added
   (dataretrieval, spotpy, s3fs matched to the venv's fsspec, rioxarray):
   re-pinning present ones (per the original Dockerfile draft) would downgrade
   the stack the engine binaries were built against.
5. **Memory requests are 14 GB, not 16** (§3 profiles): nominal 16 GB slots
   advertise 15991 MB, so a 16384 MB request matches nothing and idles forever.
6. **Fail-loud outputs** (§9): `run_nextgen` and `apply_params` write their
   declared outputs (empty tar / placeholder PNG) before any failing exit —
   without that, HTCondor holds the job on stage-out and the DAG hangs instead
   of failing visibly.
7. **Runtime expectation confirmed** (§11): demo gage `run_nextgen` ran in
   minutes on 4 cores; whole rescue-DAG segment (model + analysis + summary)
   completed well inside the predicted 20–30 min envelope.
8. **Small basins need serial NextGen** (new): PyNGIAB's parallel mode aborts in
   `partitionGenerator` when a basin has fewer catchments than cores, and
   swallows the error. `run_nextgen` detects the absent routing output and
   retries with `serial_execution_mode=True` — without this, small gages in a
   list would fail their model runs.
9. **Gate 3 passed 2026-07-30** (§12): `run_manual.sh` end to end for the demo
   gage on a 3-month slice, inside the published image. (The script needed a
   `set -o pipefail` fix — `tar -tf | head` SIGPIPEs on large tarballs.)
10. **Parallel multi-start calibration** (extends §6's "fan out multiple DDS
    trials/seeds as sibling jobs" from optional idea to implemented):
    `--dds-trials N` creates N seeded `calibrate` sibling jobs plus a
    `select_best_params` reducer that forwards the winning parameter set to
    `apply_params` and merges the iteration logs (trial column). The
    previous meaning of the flag (sequential SPOTPY trials inside one job)
    is gone. Failed trials are tolerated if at least one succeeds.
11. **Paper-comparison outcome and the calibration-objective defect**
    (2026-08-01, run0006; full analysis in `PAPER_COMPARISON.md`): the
    uncalibrated model and the NWM v3.0 benchmark reproduce the published
    values within 0.01–0.05 KGE — §11's "scientific expectation" and the
    substance of §12 gate 4 are met. The calibration leg does not reproduce:
    DDS improved its own objective (to a peak of −0.39 hourly KGE) while
    *degrading* timestamp-aligned skill, implicating the vendored
    `cal_utils` objective path — length-based obs/sim alignment and/or the
    global `realization.json` `model_params` override not reaching
    per-catchment CFE ini files in ngiab-4.9.1 configs. Diagnostic plan in
    `PAPER_COMPARISON.md`.
12. **Calibration is ON by default** (deviates from §9's "off by default";
    user decision 2026-07-31), with the paper demo's 6 repetitions —
    `--calibrate 0` disables, and DDS requires more repetitions than its ~5
    initialization samples. Its first cluster exercise exposed four wrapper
    bugs, all fixed and the full calibrate → apply_params chain validated
    offline on 2026-07-31: `cal_utils.NextGenSetup` unpickles an *hourly
    m³/s* observed-flow DataFrame (the wrapper now builds it with the
    paper's `process_usgs_streamflow()` from USGS iv data, with retry,
    instead of passing the staged daily CSV); `run_spotpy` compares
    `algorithm`/`objective_function` against uppercase literals (wrapper
    uppercases its lowercase defaults); `run_spotpy` returns a positional
    parameter vector, which the wrapper now converts to `write_config`'s
    per-module config-key dicts; and `apply_params` now applies those to
    `realization.json` via `update_parameters()` (the paper's mechanism)
    rather than globbing module ini files.
