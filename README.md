# NextGen (NGIAB) Watershed Modeling Workflow

A Pegasus WMS workflow that runs the **NOAA NextGen Water Resources Modeling
Framework** ("NextGen In A Box" / NGIAB) for any set of USGS gages in CONUS:
subset the national hydrofabric, generate AORC meteorological forcings, run
NextGen (NOAH-OWP-Modular + CFE with t-route channel routing), evaluate against
USGS observations and the NWM v3.0 retrospective (TEEHR), and calibrate
parameters with SPOTPY DDS (on by default).

Converted from the five sequential notebooks published with:

> Nassar, A., Tarboton, D., Baig, F., Cunningham, F., Patel, N., Halgren, J.,
> Lee, H., Salehabadi, H., Castronova, A., Garousi-Nejad, I. (2026).
> *A cloud-based JupyterHub platform for community research with the NextGen
> water resources modeling framework.* Environmental Modelling & Software 203,
> 107031. https://doi.org/10.1016/j.envsoft.2026.107031

Notebooks: HydroShare resource
[27045581bdea4808a393330f2417379c](https://doi.org/10.4211/hs.27045581bdea4808a393330f2417379c).
The conversion plan, including scope constraints and validation gates, is in
[`SPEC.md`](SPEC.md).

Repo: https://github.com/pegasus-isi/nextgen-workflow · License:
[Apache-2.0](LICENSE). The published paper PDF is not distributed here
(copyright); results extracted from it are cited in
[`PAPER_COMPARISON.md`](PAPER_COMPARISON.md) with the DOI.

**Status (2026-08-01): every branch validated under Pegasus, and the
paper-matching run is complete.** run0005 was the first 100%-clean DAG
(38/38 nodes: prep, model, both evaluations, calibration, calibrated
re-run, summary). run0006 then reproduced the paper's configuration
(gage 10109001, WY2018–2021, calibration split 2019-10-01, 200 DDS
iterations): the uncalibrated-model and NWM v3.0 metrics **match the
published values within 0.01–0.05 KGE** — see
[`PAPER_COMPARISON.md`](PAPER_COMPARISON.md) — which substantially delivers
what notebook-parity validation (SPEC §12 gate 4) exists to prove. One open
investigation: the vendored calibration objective misbehaves under current
NGIAB configs (Known gaps #4); notably, today's *default* parameters already
score KGE 0.86 on the paper's evaluation period, near its calibrated 0.893.

## Why a workflow

The notebooks are a linear chain for one basin at a time. The scientific premise
of the paper — running many research subdomains — is embarrassingly parallel per
gage, which is what this workflow exploits: the whole chain scatters over a gage
list, and the two evaluation branches run as parallel siblings after each model
run. **No credentials are required anywhere**; every data source is public.

## Pipeline

```
                     fetch_hydrofabric  (once, shared by all gages)
                              │
                   ┌──────────┴───────────┐
                   ▼                      ▼
            subset_hydrofabric  ...  (one chain per gage)
                   │
        ┌──────────┴──────────┐
        ▼                     ▼
 generate_forcings   generate_realization
        └──────────┬──────────┘
                   ▼
            assemble_rundir  ──►  rundir_{gage}.tar
                   │
                   ▼
              run_nextgen  ◄── fetch_usgs_obs
                   │
        ┌──────────┴──────────┐
        ▼                     ▼
 outputs_analysis      teehr_evaluation
        └──────────┬──────────┘
                   ▼            (default on, 6 reps; --calibrate 0 disables)
              calibrate ──► apply_params ──► second analysis/teehr pair
                   │
                   ▼
              summarize  (fan-in across all gages)
```

| Step | Wraps | Notes |
|------|-------|-------|
| `fetch_hydrofabric` | `ngiab_data_cli` cache warm | Downloads the multi-GB CONUS hydrofabric **once** per run |
| `subset_hydrofabric` | `ngiab_data_cli -s` | Emits `config/` with the subset GeoPackage |
| `generate_forcings` | `ngiab_data_cli -f` | AORC subset/regrid for the period |
| `generate_realization` | `ngiab_data_cli -r` + patch | Adds the 27 model `output_variables` |
| `assemble_rundir` | `tar` | Merges the prep outputs into the NGIAB layout |
| `fetch_usgs_obs` | `dataretrieval` NWIS | Public API, no key |
| `run_nextgen` | `PyNGIAB().run()` | NextGen + t-route; multiprocesses across catchments, auto-retries serially for small basins |
| `outputs_analysis` | `ngen_output_analysis()` | Basin means, RMSE/KGE, water balance |
| `teehr_evaluation` | CIROH `ngiab-teehr` container | Real TEEHR: NextGen **and NWM v3.0** scored vs USGS (KGE/NSE/bias/RMSDR). Runs in its own container; pandas fallback when the gage lacks an NWM crosswalk entry |
| `calibrate` | `run_spotpy()` DDS | **On by default** (6 repetitions, the paper demo's value; `--calibrate 0` disables) — each iteration is a full model run. `--dds-trials N` fans out N seeded trials in parallel |
| `select_best_params` | best-of reducer | Only with `--dds-trials` > 1: picks the winning trial, merges iteration logs |
| `apply_params` | param patch + re-run | Second half of the unrolled calibration cycle |
| `summarize` | fan-in merge | Combined metrics table and comparison figure |

Job count is **`12N + 2`** for N gages by default (`8N + 2` with `--calibrate 0`).

## Quick start

```sh
# 1. Build and push the container
docker build -t kthare10/nextgen-workflow:latest -f Docker/NextGen_Dockerfile .
docker push kthare10/nextgen-workflow:latest

# 2. Generate the workflow (submit host needs only pegasus-wms.api)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./workflow_generator.py --gages gage-10109001 --start 2017-10-01 --end 2021-09-30

# 3. Plan and submit
pegasus-plan --submit -s condorpool -o local workflow.yml

# 4. Monitor / debug
pegasus-status <run-dir>
pegasus-analyzer <run-dir>
```

### Options

```
--gages GAGE [GAGE ...]   Gage IDs, e.g. gage-10109001
--gages-file FILE         One gage ID per line (# comments allowed)
--start / --end           Simulation period (default 2017-10-01 .. 2021-09-30)
--training-start DATE     Spin-up / evaluation boundary (default: --start)
--calibrate N             SPOTPY DDS repetitions (default 6 — the paper demo's
                          value; 0 disables the calibration branch; serious
                          calibration wants more reps than the 17 parameters)
--dds-trials N            Independent seeded DDS trials as PARALLEL sibling
                          jobs; a select_best_params reducer picks the winner
                          (default 1 = single sequential trajectory). N trials
                          use N worker slots without extending wall time
--hydrofabric-tar PATH    Reuse an existing hydrofabric cache; drops the fetch job
--container-image URI     Override the main container image
--teehr-image URI         Override the teehr_evaluation container
                          (default docker://kthare10/nextgen-teehr:x86)
-e / --execution-site-name, -o / --output, -s / --skip-sites-catalog
```

Multiple basins, reusing a cache you already have:

```sh
./workflow_generator.py --gages-file config/gages.txt \
    --hydrofabric-tar ~/hydrofabric_cache.tar
```

## Validation

Gates from `SPEC.md` §12, with current status:

```sh
# Gate 1 (PASSING, 19 tests): generator unit tests — DAG shape, staging
# strategy, and the missing-producer check
pip install pytest pyyaml && python -m pytest tests/ -v

# Gate 3: run every step by hand on a 3-month slice, inside the container
# (superseded in practice by the successful full cluster run on 2026-07-30,
# but still the cheapest way to shake out a change to a single wrapper)
docker run --rm -it -v "$PWD":/work -w /work \
    kthare10/nextgen-workflow:latest bash run_manual.sh
```

The strongest correctness check (gate 4) is **notebook parity**: proving the
extraction from notebooks to CLI wrappers did not change the science. It is
**substantially addressed** by the paper-matching run — see
[`PAPER_COMPARISON.md`](PAPER_COMPARISON.md): uncalibrated-model and NWM v3.0
metrics reproduce the published Fig. 10 values within 0.01–0.05 KGE. The
formal variant (execute notebooks 2–3, diff CSVs to float tolerance) remains
undone; if it is ever run and diffs, suspect the `ngiab_data_preprocess==4.9.1`
pin first (newer than the paper's version).

## Outputs

Staged to `output/`:

```
output/
├── analysis/{gage}_basin_means.csv      # area-weighted basin means
├── analysis/{gage}_metrics.csv          # RMSE, KGE vs USGS
├── analysis/{gage}_teehr_metrics.csv    # TEEHR: ngen + NWM v3.0 vs USGS (full
│                                        # period); pandas fallback: skill by
│                                        # spin-up/evaluation period — see the
│                                        # `method` column
├── plots/{gage}_hydrograph.png
├── plots/{gage}_water_balance.png
├── plots/{gage}_teehr.png
├── {gage}_usgs_obs.csv
├── calibration/{gage}_calibration_iterations.csv   # unless --calibrate 0
├── {gage}_best_params.json                         # unless --calibrate 0
└── summary/summary_metrics.{csv,png}    # fan-in across all gages
```

Run-directory tarballs stay in scratch (`stage_out=False`) — they are large
intermediates, not deliverables.

## Design notes

**Run directories travel as tarballs.** The notebooks pass state through a
mutable directory tree and edit `realization.json` in place. Here each stage
consumes one tarball and emits a new one (`rundir` → `rundir_run` →
`rundir_cal`), so nothing is mutated across a job boundary and every stage's tree
is separately archived. `assemble_rundir` exists because the prep branches emit
loose subtrees that something has to merge — without it, `run_nextgen` would
declare an input no job produces.

**Vendored notebook utilities.** `bin/lib/` holds the paper's own utility modules
(`ngen_outputs_utils.py`, `forcings_utils.py`, `cal_utils.py`, `ngiab_utils.py`)
so the analysis and calibration math matches the publication. They are registered
in the Replica Catalog and staged into each job. Two small patches were needed,
both marked `PEGASUS PATCH` in the source: the hardcoded
`/home/jovyan/ngiab_preprocess_output` root now reads `NGIAB_DATA_ROOT`, and a
`/home/jovyan` fallback CWD became `/tmp`. `bin/lib/ngiab_pegasus.py` is new — it
holds the tarball plumbing, the private per-job `$HOME` (NGIAB caches into
`$HOME/.ngiab`, and concurrent jobs must not share it), and retry helpers.

**No hidden state.** The notebooks hardcoded values copied by hand between cells,
notably the routing `feature_id`. Here it is derived from the hydrofabric at run
time via `ngiab_utils.get_gages_from_hydrofabric()`.

**Interactive output dropped.** Folium maps and hvplot/bokeh widgets are replaced
by static PNGs; plotting failures degrade to a placeholder image rather than
failing the job, so a cosmetic problem never kills a model run.

**The container diverges from the paper's JupyterHub in documented ways.** The
NGIAB base image is Rocky Linux (`dnf`, no pip in the venv — installs go through
its bundled `uv`) and does **not** ship `ngiab_data_cli` or `pyngiab`; both are
installed by the Dockerfile. `ngiab_data_preprocess` gets an isolated venv at
`/opt/preprocess` (its pins conflict with the engine venv), found by wrappers via
`NGIAB_CLI_PYTHON`; its interactive hydrofabric-download prompt has no
non-interactive flag, so `run_ngiab_cli()` answers it via stdin. `pyngiab` is
installed from the paper authors' repo pinned to commit `f17e6fc`, **with a
one-line `PEGASUS PATCH`** for an upstream bug: its dependency check can only
return True via the venv-retry branch, so an environment that is correct on the
first probe gets rejected. The Dockerfile asserts the patch still applies and
constructs `PyNGIAB` at build time. Only packages absent from the base venv are
added — re-pinning present ones (pandas, xarray, pyarrow, …) would downgrade the
stack the engine was built against.

**Memory requests are 14 GB, not 16.** Nominal "16 GB" worker slots advertise
15991 MB after the OS takes its share; a 16384 MB request matches zero slots and
idles forever.

**`teehr_evaluation` runs in a second container.** TEEHR cannot be installed
next to the NGIAB engine (its pins downgrade pyarrow/pydantic/zarr under ngen),
so the transformation catalog gives that one job `kthare10/nextgen-teehr:x86`
(`--teehr-image` overrides) — a thin derivative of CIROH's companion image
`awiciroh/ngiab-teehr:x86` (the same split NGIAB's own `runTeehr.sh` uses) that
adds only curl/wget, which PegasusLite needs to fetch its worker package inside
the container (`Docker/Teehr_Dockerfile`). The wrapper drives the image's bundled
`/app/teehr_ngen.py` (symlinks the extracted run dir to `./data`, calls its
`main()`), then maps `teehr/metrics.csv` into this workflow's schema. Validated
end to end on a real run directory: NextGen and NWM v3.0 retrospective scored
against USGS observations in one table. The image has no matplotlib, so the
metric plot degrades to a Pillow-rendered table there.

**Small basins auto-retry serially.** PyNGIAB's parallel mode partitions across
all cores; a basin with fewer catchments than cores makes `partitionGenerator`
abort — and PyNGIAB swallows the error. `run_nextgen` detects the missing
routing output and reruns with `serial_execution_mode=True` before failing.

**Calibration parallelizes as multi-start, not within a trajectory.** A DDS
trajectory is inherently sequential — each iteration perturbs the best result
so far — so `--dds-trials N` runs N independently-seeded trajectories as
sibling jobs and a `select_best_params` reducer forwards the winner to
`apply_params` (failed trials are tolerated as long as one succeeds; the
merged iteration log keeps every trial, tagged by a `trial` column). Five
40-iteration trials finish in roughly a fifth the wall time of one
200-iteration trajectory; they explore differently — DDS scales its
perturbation schedule to each trajectory's budget — but multi-start short-DDS
is competitive in practice and more robust to a bad initialization. This is
the knob the paper's own discussion (§4.4) wished for on JupyterHub.

## Known gaps

Updated after the first successful cluster run (2026-07-30) and the TEEHR and
calibration validation (2026-07-31). The scaffold's original gaps were
confirmed or resolved by those runs and have moved to the design notes; what
remains:

1. ~~TEEHR's NWM comparison only covers crosswalked gages~~ **Resolved
   2026-07-31: every gage gets the NWM v3.0 benchmark.** Gages in TEEHR's
   USGS↔NWM crosswalk (6,903 of the hydrofabric's 17,637) get it through
   TEEHR; all others — including the paper's demo gage 10109001 — get it
   through the pandas path, which resolves the gage's reach via the USGS
   NLDI API (the paper's own mechanism; hydrofabric `hf_id` is a
   reference-fabric id, NOT an NWM feature id) and reads NOAA's public
   NWM v3.0 retrospective zarr directly. The `method` column records which
   path produced the numbers; the NWM comparison is best-effort and never
   fails the job.
2. ~~Calibration untested through Pegasus~~ **Resolved mechanically**: the
   calibrate → apply_params → re-evaluation chain completed cleanly inside
   full Pegasus runs (run0005 with 6 repetitions, run0006 with 200).
   Calibration is **on by default** (6 repetitions — DDS needs more than its
   ~5 initialization samples, which is why the paper's demo used 6;
   `--calibrate 0` disables, `--dds-trials N` parallelizes). The *scientific*
   quality of what it optimizes is gap #4.
3. **Notebook parity (SPEC §12 gate 4) is substantially — not formally —
   addressed.** The paper-matching run (run0006, 2026-08-01; see
   [`PAPER_COMPARISON.md`](PAPER_COMPARISON.md)) reproduced the published
   uncalibrated-model and NWM v3.0 metrics within 0.01–0.05 KGE, which is the
   evidence gate 4 exists to provide. Formally executing notebooks 2–3 and
   diffing CSVs remains undone.
4. **Calibration can install parameters worse than the defaults.** run0006's
   200-iteration DDS reached hourly KGE 0.610 but never beat today's
   excellent default parameterization (0.860) — and the vendored calibration
   loop never evaluates the untouched baseline as a candidate, so the
   inferior "winner" was installed anyway. A code review also confirmed an
   unconditional off-by-one in the objective's positional obs/sim alignment
   and a destructive `model_params` replace; whether the global
   `realization.json` override even reaches per-catchment CFE inis in
   ngiab-4.9.1 configs is still unverified. Full findings and the revised
   fix plan are in `PAPER_COMPARISON.md`.
