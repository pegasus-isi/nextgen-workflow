#!/usr/bin/env python3

"""
Pegasus workflow generator for the NextGen (NGIAB) watershed modeling pipeline.

Converts the five sequential notebooks from Nassar et al. 2026 (Environmental
Modelling & Software 203, 107031) into a Pegasus DAG that scatters over USGS gages.

Pipeline steps (per gage):
1. subset_hydrofabric   - ngiab_data_cli -s : subset the CONUS hydrofabric at a gage
2. generate_forcings    - ngiab_data_cli -f : subset/regrid AORC meteorological forcings
3. generate_realization - ngiab_data_cli -r : realization.json + module configs
4. assemble_rundir      - merge the three prep outputs into one NGIAB run directory
5. run_nextgen          - PyNGIAB().run() : NOAH-OWP + CFE + t-route routing
6. outputs_analysis     - basin-mean aggregation, RMSE/KGE vs USGS observations
7. teehr_evaluation     - TEEHR/Spark evaluation against NWM v3.0 retrospective
8. calibrate            - (optional) SPOTPY DDS calibration of the baseline run
9. summarize            - fan-in across all gages

The CONUS hydrofabric is fetched by a single shared job and reused by every gage.

Usage:
    ./workflow_generator.py --gages gage-10109001 --start 2017-10-01 --end 2021-09-30
    ./workflow_generator.py --gages-file config/gages.txt --calibrate 6

    # A centrally hosted site catalog, or a plain HTCondor pool:
    ./workflow_generator.py --gages gage-10109001 -s unity.yml
    ./workflow_generator.py --gages gage-10109001 --calibrate 0 -e condorpool

Sites follow pegasus-isi/pegasus-gromacs: jobs run on a site named "compute",
defined by a centrally hosted site catalog (-s access-pegasus.yml, ...;
https://github.com/pegasushub/pegasus-site-catalogs) or by one in
~/.pegasusrc. The generator writes no site catalog and never submits; it
prints the pegasus-plan command. NextGen-Workflow.ipynb drives the same class
interactively.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from Pegasus.api import *

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# Per-tool resource configuration for the transformation catalog. The NextGen
# run multiprocesses across catchments; TEEHR starts a local Spark session and
# needs Java plus headroom. runtime (wall-clock seconds) is set only on tools
# that can outlast the ~2 h hosted batch catalogs give a job by default: the
# demo basin's model run takes 5-7 minutes, but larger basins, longer periods
# and multi-GB downloads (hydrofabric, AORC forcings) take far longer.
# calibrate's budget is raised with --calibrate (CALIBRATE_SECONDS_PER_REPETITION).
TOOL_CONFIGS = {
    "fetch_hydrofabric": {"memory": "8 GB", "cores": 1, "runtime": 4 * 3600},
    "subset_hydrofabric": {"memory": "8 GB", "cores": 1},
    "generate_forcings": {"memory": "12 GB", "cores": 2, "runtime": 4 * 3600},
    "generate_realization": {"memory": "4 GB", "cores": 1},
    "assemble_rundir": {"memory": "2 GB", "cores": 1},
    "fetch_usgs_obs": {"memory": "2 GB", "cores": 1},
    # 14 GB, not 16: the pool's "16 GB" workers advertise 15991 MB after the OS
    # takes its share, so a 16384 MB request matches zero slots and idles forever.
    #
    # 8 cores, not 4, for the three PyNGIAB jobs: parallel mode partitions the
    # basin across ALL cores of the node and launches one MPI rank per
    # partition, ignoring what the job requested. A 4-core request on an
    # 8-core node therefore runs 8 ranks inside a 4-CPU cgroup, and the
    # resulting CPU starvation is a good way to trip MPICH's nemesis TCP
    # module (`socksm.c:569` assertion) — which is what killed calibrate
    # trial 5 twice in run0003. Match the request to what ngen actually
    # spawns. On a 15.6 GB node the 14 GB request already limits these to one
    # per node, so widening cores costs no throughput.
    "run_nextgen": {"memory": "14 GB", "cores": 8, "runtime": 4 * 3600},
    "outputs_analysis": {"memory": "8 GB", "cores": 1},
    "teehr_evaluation": {"memory": "12 GB", "cores": 2},
    "calibrate": {"memory": "14 GB", "cores": 8, "runtime": 4 * 3600},
    "select_best_params": {"memory": "2 GB", "cores": 1},
    "apply_params": {"memory": "14 GB", "cores": 8, "runtime": 4 * 3600},
    "summarize": {"memory": "2 GB", "cores": 1},
}

# Wall-clock budget per DDS repetition for a calibrate job: each repetition is
# a full model run (5-7 minutes on the demo basin), so allow about twice that.
# The calibrate transformation's runtime is max(TOOL_CONFIGS value,
# (repetitions + 1) x this) — the +1 covers the untouched-baseline candidate
# the job also evaluates. It is set in the Transformation Catalog, not on the
# jobs: TC profiles take precedence over job profiles at plan time.
CALIBRATE_SECONDS_PER_REPETITION = 15 * 60

# Support modules vendored from the paper's HydroShare resource. These are called
# by the wrapper scripts, so they belong in the Replica Catalog (not the
# Transformation Catalog) and are staged in as job inputs.
SUPPORT_LIBS = [
    "ngiab_pegasus.py",
    "ngen_outputs_utils.py",
    "forcings_utils.py",
    "cal_utils.py",
    "ngiab_utils.py",
]


class NextGenWorkflow:
    """NextGen/NGIAB watershed modeling workflow, scattered per USGS gage."""

    wf = None
    sc = None
    tc = None
    rc = None
    props = None

    dagfile = None
    wf_dir = None
    shared_scratch_dir = None
    local_storage_dir = None
    wf_name = "nextgen"
    # Set by main() from --calibrate; sizes the calibrate runtime budget.
    calibrate_repetitions = 0

    def __init__(self, dagfile="workflow.yml", container_image=None,
                 teehr_image=None):
        self.dagfile = dagfile
        self.wf_dir = str(Path(__file__).parent.resolve())
        self.shared_scratch_dir = os.path.join(self.wf_dir, "scratch")
        self.local_storage_dir = os.path.join(self.wf_dir, "output")
        self.container_image = container_image or "Apptainer/NextGen_Container.sif"
        # TEEHR cannot coexist with the NGIAB engine environment (its pins
        # would downgrade pyarrow/pydantic/zarr under ngen), so the
        # teehr_evaluation job runs in a derivative of CIROH's companion
        # evaluation image instead — the same split NGIAB itself uses
        # (runTeehr.sh). Our derivative (Apptainer/Teehr_Container.def) only
        # adds curl/wget, which PegasusLite needs to fetch its worker package
        # inside the container.
        self.teehr_image = teehr_image or "Apptainer/Teehr_Container.sif"

    def _image(self, image, def_name):
        """Resolve a container reference to an (image_url, image_site) pair.

        A path ending in .sif (the default) is a locally built Apptainer image;
        Pegasus stages it like any other input, so image_site is "local" — the
        site where the file physically lives. Relative paths resolve against the
        workflow directory. A full URL is passed through unchanged, and a bare
        name means Docker Hub, so a registry image still works.

        The .sif suffix is the discriminator on purpose: a bare registry
        reference like "kthare10/nextgen-workflow:latest" also contains a slash,
        so testing for a path separator would misread it as a local file.
        """
        if "://" in image:
            return image, {"http": "web", "https": "web", "file": "local"}.get(
                image.split("://", 1)[0], "docker_hub")
        if not image.endswith(".sif"):
            return "docker://" + image, "docker_hub"
        path = image if os.path.isabs(image) else os.path.join(self.wf_dir, image)
        if not os.path.exists(path):
            logger.warning(
                "Apptainer image not found at %s — build it first with: "
                "apptainer build %s Apptainer/%s", path, path, def_name)
        return "file://" + path, "local"

    def write(self):
        if self.sc is not None:
            self.sc.write()
        self.props.write()
        self.rc.write()
        self.tc.write()
        self.wf.write(file=self.dagfile)

    # ------------------------------------------------------------------
    # Plan / run / monitor (thin wrappers over the Pegasus API Workflow
    # object, for interactive use e.g. from a Jupyter notebook)
    # ------------------------------------------------------------------
    def plan_submit(self, exec_site_name="compute", raise_errors=False):
        try:
            self.wf.plan(
                dir="submit",
                sites=[exec_site_name],
                output_sites=["local"],
                cleanup="none",
                verbose=1,
                submit=True,
            )
        except PegasusClientError as e:
            print(e)
            if raise_errors:
                raise

    def status(self):
        try:
            self.wf.status(long=True)
        except PegasusClientError as e:
            print(e)

    def wait(self):
        try:
            self.wf.wait()
        except PegasusClientError as e:
            print(e)

    def statistics(self):
        try:
            self.wf.statistics()
        except PegasusClientError as e:
            print(e)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    def create_pegasus_properties(self, hosted_site_catalog=None):
        self.props = Properties()
        self.props["pegasus.transfer.threads"] = "16"
        # The hydrofabric cache and run-directory tarballs are large; give
        # integrity checking a chance to be skipped on the biggest transfers.
        self.props["pegasus.integrity.checking"] = "nosymlink"
        if hosted_site_catalog:
            # Use one of Pegasus' centrally hosted site catalogs instead of
            # a locally generated one. pegasus-plan downloads and caches the
            # named file from the catalog repository at plan time.
            # https://pegasus.isi.edu/documentation/reference-guide/catalogs.html#centrally-hosted-site-catalogs
            self.props["pegasus.catalog.site.repo.file"] = hosted_site_catalog

    # ------------------------------------------------------------------
    # Site Catalog
    #
    # Not used by the CLI below by default — pegasus-plan resolves the site
    # catalog from a centrally hosted one instead (see -s/--hosted-site-catalog
    # and create_pegasus_properties above). Kept for programmatic/notebook use
    # when a self-contained, locally generated HTCondor site catalog is wanted.
    # ------------------------------------------------------------------
    def create_sites_catalog(self, exec_site_name="compute"):
        self.sc = SiteCatalog()

        local = Site("local").add_directories(
            Directory(
                Directory.SHARED_SCRATCH, self.shared_scratch_dir
            ).add_file_servers(
                FileServer("file://" + self.shared_scratch_dir, Operation.ALL)
            ),
            Directory(
                Directory.LOCAL_STORAGE, self.local_storage_dir
            ).add_file_servers(
                FileServer("file://" + self.local_storage_dir, Operation.ALL)
            ),
        )

        exec_site = (
            Site(exec_site_name)
            .add_condor_profile(universe="vanilla")
            .add_pegasus_profile(style="condor")
        )

        self.sc.add_sites(local, exec_site)

    # ------------------------------------------------------------------
    # Transformation Catalog
    # ------------------------------------------------------------------
    def create_transformation_catalog(self, exec_site_name="compute"):
        """Containers and transformations, registered on the execution site;
        the scripts and .sif images live on "local" (the submit host) and are
        staged."""
        self.tc = TransformationCatalog()

        ngen_url, ngen_site = self._image(
            self.container_image, "NextGen_Container.def")
        teehr_url, teehr_site = self._image(
            self.teehr_image, "Teehr_Container.def")

        container = Container(
            "nextgen_container",
            container_type=Container.SINGULARITY,
            image=ngen_url,
            image_site=ngen_site,
        )
        teehr_container = Container(
            "teehr_container",
            container_type=Container.SINGULARITY,
            image=teehr_url,
            image_site=teehr_site,
        )

        transformations = []
        for tool_name, config in TOOL_CONFIGS.items():
            tool_container = (
                teehr_container if tool_name == "teehr_evaluation" else container
            )
            tx = Transformation(
                tool_name,
                site=exec_site_name,
                pfn=os.path.join(self.wf_dir, f"bin/{tool_name}.py"),
                is_stageable=True,
                container=tool_container,
            ).add_pegasus_profile(
                memory=config["memory"],
                cores=config.get("cores", 1),
            )
            runtime = config.get("runtime")
            if tool_name == "calibrate":
                runtime = max(runtime, (self.calibrate_repetitions + 1)
                              * CALIBRATE_SECONDS_PER_REPETITION)
            if runtime:
                tx.add_pegasus_profile(runtime=runtime)
            transformations.append(tx)

        self.tc.add_containers(container, teehr_container)
        self.tc.add_transformations(*transformations)

    # ------------------------------------------------------------------
    # Replica Catalog
    # ------------------------------------------------------------------
    def create_replica_catalog(self, hydrofabric_tar=None):
        self.rc = ReplicaCatalog()

        # Support libraries called by the wrappers (not transformations).
        for lib in SUPPORT_LIBS:
            path = os.path.join(self.wf_dir, "bin", "lib", lib)
            self.rc.add_replica("local", lib, "file://" + path)

        # An already-downloaded hydrofabric cache can be supplied instead of
        # letting the workflow fetch it (saves a multi-GB download per run).
        if hydrofabric_tar:
            self.rc.add_replica(
                "local",
                "hydrofabric_cache.tar",
                "file://" + os.path.abspath(hydrofabric_tar),
            )

    # ------------------------------------------------------------------
    # Workflow DAG
    # ------------------------------------------------------------------
    def create_workflow(self, args):
        self.wf = Workflow(self.wf_name, infer_dependencies=True)

        lib_files = [File(lib) for lib in SUPPORT_LIBS]
        hydrofabric_cache = File("hydrofabric_cache.tar")

        # ---- Shared: fetch the CONUS hydrofabric exactly once -------------
        # Registered in the Replica Catalog when --hydrofabric-tar is given, in
        # which case no fetch job is created at all.
        if not args.hydrofabric_tar:
            fetch_hf_job = (
                Job("fetch_hydrofabric", _id="fetch_hydrofabric",
                    node_label="fetch_hydrofabric")
                .add_args(
                    "--gage", args.gages[0],
                    "--output", hydrofabric_cache,
                )
                .add_inputs(*lib_files)
                .add_outputs(
                    hydrofabric_cache, stage_out=False, register_replica=False
                )
                .add_dagman_profile(retry="2")
            )
            self.wf.add_jobs(fetch_hf_job)

        # ---- Per-gage pipelines -------------------------------------------
        metrics_files = []
        for gage in args.gages:
            metrics_files.extend(
                self._add_gage_pipeline(gage, args, hydrofabric_cache, lib_files)
            )

        # ---- Fan-in summary ----------------------------------------------
        summary_csv = File("summary/summary_metrics.csv")
        summary_png = File("summary/summary_metrics.png")
        # Explicit repeated --metrics arguments: never let the merge job discover
        # its inputs by scanning a directory, since Pegasus stages files singly.
        metric_args = []
        for metrics_file in metrics_files:
            metric_args.extend(["--metrics", metrics_file])
        summarize_job = (
            Job("summarize", _id="summarize", node_label="summarize")
            .add_args(
                *metric_args,
                "--output-csv", summary_csv,
                "--output-plot", summary_png,
            )
            .add_inputs(*metrics_files, *lib_files)
            .add_outputs(summary_csv, stage_out=True, register_replica=False)
            .add_outputs(summary_png, stage_out=True, register_replica=False)
        )
        self.wf.add_jobs(summarize_job)

    def _add_gage_pipeline(self, gage, args, hydrofabric_cache, lib_files):
        """Add the job chain for one gage. Returns its metrics File objects."""
        safe = gage.replace("-", "_")

        # --- Prep: subset -> (forcings || realization) -> assemble ---------
        subset_tar = File(f"{gage}_subset.tar")
        subset_job = (
            Job("subset_hydrofabric", _id=f"subset_{safe}",
                node_label=f"subset_{gage}")
            .add_args(
                "--gage", gage,
                "--hydrofabric-cache", hydrofabric_cache,
                "--output", subset_tar,
            )
            .add_inputs(hydrofabric_cache, *lib_files)
            .add_outputs(subset_tar, stage_out=False, register_replica=False)
            .add_pegasus_profiles(label=gage)
        )
        self.wf.add_jobs(subset_job)

        forcings_tar = File(f"{gage}_forcings.tar")
        forcings_job = (
            Job("generate_forcings", _id=f"forcings_{safe}",
                node_label=f"forcings_{gage}")
            .add_args(
                "--gage", gage,
                "--subset-tar", subset_tar,
                "--start", args.start,
                "--end", args.end,
                "--output", forcings_tar,
            )
            .add_inputs(subset_tar, *lib_files)
            .add_outputs(forcings_tar, stage_out=False, register_replica=False)
            .add_pegasus_profiles(label=gage)
        )
        self.wf.add_jobs(forcings_job)

        # NOTE: `ngiab_data_cli -r` takes the same --start/--end as -f and in the
        # notebook does not read the forcings file, so realization generation runs
        # as a parallel sibling of forcings. If a real run shows that -r requires
        # forcings/forcings.nc to exist, add `forcings_tar` to this job's inputs
        # and pass --forcings-tar; that single change serializes the two steps.
        realization_tar = File(f"{gage}_realization.tar")
        realization_job = (
            Job("generate_realization", _id=f"realization_{safe}",
                node_label=f"realization_{gage}")
            .add_args(
                "--gage", gage,
                "--subset-tar", subset_tar,
                "--start", args.start,
                "--end", args.end,
                "--output", realization_tar,
            )
            .add_inputs(subset_tar, *lib_files)
            .add_outputs(realization_tar, stage_out=False, register_replica=False)
            .add_pegasus_profiles(label=gage)
        )
        self.wf.add_jobs(realization_job)

        rundir_tar = File(f"rundir_{gage}.tar")
        assemble_job = (
            Job("assemble_rundir", _id=f"assemble_{safe}",
                node_label=f"assemble_{gage}")
            .add_args(
                "--gage", gage,
                "--subset-tar", subset_tar,
                "--forcings-tar", forcings_tar,
                "--realization-tar", realization_tar,
                "--output", rundir_tar,
            )
            .add_inputs(subset_tar, forcings_tar, realization_tar, *lib_files)
            .add_outputs(rundir_tar, stage_out=False, register_replica=False)
            .add_pegasus_profiles(label=gage)
        )
        self.wf.add_jobs(assemble_job)

        # --- Observations (independent of the model chain) ------------------
        obs_csv = File(f"{gage}_usgs_obs.csv")
        obs_job = (
            Job("fetch_usgs_obs", _id=f"obs_{safe}", node_label=f"obs_{gage}")
            .add_args(
                "--gage", gage,
                "--start", args.start,
                "--end", args.end,
                "--output", obs_csv,
            )
            .add_inputs(*lib_files)
            .add_outputs(obs_csv, stage_out=True, register_replica=False)
            .add_pegasus_profiles(label=gage)
            .add_dagman_profile(retry="2")
        )
        self.wf.add_jobs(obs_job)

        # --- Model run ------------------------------------------------------
        run_tar = File(f"rundir_{gage}_run.tar")
        hydrograph_png = File(f"plots/{gage}_hydrograph.png")
        run_job = (
            Job("run_nextgen", _id=f"run_{safe}", node_label=f"run_{gage}")
            .add_args(
                "--gage", gage,
                "--rundir-tar", rundir_tar,
                "--obs", obs_csv,
                "--output", run_tar,
                "--plot", hydrograph_png,
            )
            .add_inputs(rundir_tar, obs_csv, *lib_files)
            .add_outputs(run_tar, stage_out=False, register_replica=False)
            .add_outputs(hydrograph_png, stage_out=True, register_replica=False)
            .add_pegasus_profiles(label=gage)
        )
        self.wf.add_jobs(run_job)

        # --- Evaluation branches (parallel siblings) ------------------------
        metrics_files = []
        analysis_metrics = self._add_analysis_jobs(
            gage, safe, args, run_tar, obs_csv, lib_files, suffix=""
        )
        metrics_files.extend(analysis_metrics)

        # --- Optional calibration branch ------------------------------------
        # Calibration descends from run_nextgen (it consumes run_tar, not the
        # assembled rundir_tar): the paper's sequence is prep -> baseline run
        # -> calibrate -> calibrated run, confirmed by the author in review
        # (AUTHOR_REVIEW.md #1). The post-run tarball carries the same
        # config/forcings tree plus baseline outputs, which calibration's
        # per-iteration re-runs simply overwrite.
        if args.calibrate:
            best_params = File(f"{gage}_best_params.json")
            cal_iters = File(f"calibration/{gage}_calibration_iterations.csv")

            def _calibrate_job(job_id, label, params_file, iters_file,
                               stage, seed=None):
                extra = [] if seed is None else ["--seed", str(seed)]
                job = (
                    Job("calibrate", _id=job_id, node_label=label)
                    .add_args(
                        "--gage", gage,
                        "--rundir-tar", run_tar,
                        "--obs", obs_csv,
                        "--start", args.start,
                        "--end", args.end,
                        "--training-start", args.training_start or args.start,
                        "--repetitions", str(args.calibrate),
                        "--dds-trials", "1",
                        *extra,
                        "--output-params", params_file,
                        "--output-iterations", iters_file,
                    )
                    .add_inputs(run_tar, obs_csv, *lib_files)
                    .add_outputs(params_file, stage_out=stage,
                                 register_replica=False)
                    .add_outputs(iters_file, stage_out=stage,
                                 register_replica=False)
                    .add_pegasus_profiles(label=gage)
                )
                self.wf.add_jobs(job)
                return job

            if args.dds_trials > 1:
                # A DDS trajectory is inherently sequential (each iteration
                # perturbs the running best), so parallelize as independent
                # seeded trials — sibling jobs — and let a reducer pick the
                # winner. Trial outputs are intermediates; only the merged
                # log and the winning parameters are staged out.
                trial_params, trial_iters = [], []
                for trial in range(1, args.dds_trials + 1):
                    t_params = File(f"{gage}_best_params_trial{trial}.json")
                    t_iters = File(
                        f"calibration/{gage}_calibration_iterations_"
                        f"trial{trial}.csv"
                    )
                    _calibrate_job(
                        f"calibrate_{safe}_t{trial}",
                        f"calibrate_{gage}_t{trial}",
                        t_params, t_iters, stage=False, seed=trial,
                    )
                    trial_params.append(t_params)
                    trial_iters.append(t_iters)

                select_job = (
                    Job("select_best_params", _id=f"selectbest_{safe}",
                        node_label=f"selectbest_{gage}")
                    .add_args(
                        "--gage", gage,
                        "--trial-params", *trial_params,
                        "--trial-iterations", *trial_iters,
                        "--output-params", best_params,
                        "--output-iterations", cal_iters,
                    )
                    .add_inputs(*trial_params, *trial_iters, *lib_files)
                    .add_outputs(best_params, stage_out=True,
                                 register_replica=False)
                    .add_outputs(cal_iters, stage_out=True,
                                 register_replica=False)
                    .add_pegasus_profiles(label=gage)
                )
                self.wf.add_jobs(select_job)
            else:
                _calibrate_job(f"calibrate_{safe}", f"calibrate_{gage}",
                               best_params, cal_iters, stage=True)

            cal_run_tar = File(f"rundir_{gage}_cal.tar")
            apply_job = (
                Job("apply_params", _id=f"apply_{safe}",
                    node_label=f"apply_{gage}")
                .add_args(
                    "--gage", gage,
                    "--rundir-tar", run_tar,
                    "--params", best_params,
                    "--output", cal_run_tar,
                )
                .add_inputs(run_tar, best_params, *lib_files)
                .add_outputs(cal_run_tar, stage_out=False, register_replica=False)
                .add_pegasus_profiles(label=gage)
            )
            self.wf.add_jobs(apply_job)

            # Re-evaluate the calibrated run through the same two analysis jobs.
            metrics_files.extend(
                self._add_analysis_jobs(
                    gage, safe, args, cal_run_tar, obs_csv, lib_files,
                    suffix="_cal",
                )
            )

        return metrics_files

    def _add_analysis_jobs(self, gage, safe, args, run_tar, obs_csv, lib_files,
                           suffix=""):
        """Add the outputs_analysis + teehr_evaluation sibling pair.

        Used twice when calibration is enabled: once for the default-parameter
        run and once for the calibrated run (suffix='_cal').
        """
        basin_csv = File(f"analysis/{gage}_basin_means{suffix}.csv")
        metrics_csv = File(f"analysis/{gage}_metrics{suffix}.csv")
        wb_png = File(f"plots/{gage}_water_balance{suffix}.png")
        analysis_job = (
            Job("outputs_analysis", _id=f"analysis_{safe}{suffix}",
                node_label=f"analysis_{gage}{suffix}")
            .add_args(
                "--gage", gage,
                "--run-tar", run_tar,
                "--obs", obs_csv,
                "--output-basin-means", basin_csv,
                "--output-metrics", metrics_csv,
                "--output-plot", wb_png,
            )
            .add_inputs(run_tar, obs_csv, *lib_files)
            .add_outputs(basin_csv, stage_out=True, register_replica=False)
            .add_outputs(metrics_csv, stage_out=True, register_replica=False)
            .add_outputs(wb_png, stage_out=True, register_replica=False)
            .add_pegasus_profiles(label=gage)
        )
        self.wf.add_jobs(analysis_job)

        teehr_csv = File(f"analysis/{gage}_teehr_metrics{suffix}.csv")
        teehr_png = File(f"plots/{gage}_teehr{suffix}.png")
        teehr_job = (
            Job("teehr_evaluation", _id=f"teehr_{safe}{suffix}",
                node_label=f"teehr_{gage}{suffix}")
            .add_args(
                "--gage", gage,
                "--run-tar", run_tar,
                "--obs", obs_csv,
                "--start", args.start,
                "--end", args.end,
                "--training-start", args.training_start or args.start,
                "--output-metrics", teehr_csv,
                "--output-plot", teehr_png,
            )
            .add_inputs(run_tar, obs_csv, *lib_files)
            .add_outputs(teehr_csv, stage_out=True, register_replica=False)
            .add_outputs(teehr_png, stage_out=True, register_replica=False)
            .add_pegasus_profiles(label=gage)
            .add_dagman_profile(retry="2")
        )
        self.wf.add_jobs(teehr_job)

        return [metrics_csv, teehr_csv]


# ======================================================================
# main()
# ======================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Generate the NextGen (NGIAB) Pegasus workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single gage, the paper's demo basin and period
  %(prog)s --gages gage-10109001 --start 2017-10-01 --end 2021-09-30

  # Several gages from a file, reusing a pre-downloaded hydrofabric cache
  %(prog)s --gages-file config/gages.txt --hydrofabric-tar ~/hydrofabric_cache.tar

  # Enable the SPOTPY DDS calibration branch (expensive: a model run per iteration)
  %(prog)s --gages gage-10109001 --calibrate 6 --training-start 2020-10-01

  # A centrally hosted site catalog, or a plain HTCondor pool (no catalog)
  %(prog)s --gages gage-10109001 -s unity.yml
  %(prog)s --gages gage-10109001 --calibrate 0 -e condorpool

Writes the workflow and its catalogs; it does not plan or submit. Plan with
the command it prints, or from NextGen-Workflow.ipynb (plan_submit()).
""",
    )

    # --- Standard Pegasus arguments ---
    parser.add_argument("-s", "--hosted-site-catalog", metavar="FILE",
                        type=str, default=None,
                        help="Name of a Pegasus centrally hosted site catalog "
                             "to plan against (e.g. access-pegasus.yml), "
                             "instead of a locally generated one. Sets "
                             "pegasus.catalog.site.repo.file; see "
                             "https://pegasus.isi.edu/documentation/"
                             "reference-guide/catalogs.html"
                             "#centrally-hosted-site-catalogs")
    # --execution-site is kept as an alias: earlier releases used that name.
    parser.add_argument("-e", "--execution-site-name", "--execution-site",
                        dest="execution_site_name", metavar="STR", type=str,
                        default="compute",
                        help="Execution site name (default: compute; "
                             "condorpool on a plain HTCondor pool with no "
                             "site catalog)")
    parser.add_argument("-o", "--output", metavar="STR", type=str,
                        default="workflow.yml",
                        help="Output file (default: workflow.yml)")

    # --- Workflow-specific arguments ---
    parser.add_argument("--gages", type=str, nargs="+",
                        help="Hydrofabric gage IDs, e.g. gage-10109001")
    parser.add_argument("--gages-file", type=str,
                        help="File with one gage ID per line (# comments allowed)")
    parser.add_argument("--start", type=str, default="2017-10-01",
                        help="Simulation start date YYYY-MM-DD (default: 2017-10-01)")
    parser.add_argument("--end", type=str, default="2021-09-30",
                        help="Simulation end date YYYY-MM-DD (default: 2021-09-30)")
    parser.add_argument("--training-start", type=str, default=None,
                        help="Calibration/evaluation period start (default: --start)")
    parser.add_argument("--calibrate", type=int, default=6, metavar="N",
                        help="SPOTPY DDS repetitions for the calibration branch "
                             "(default: 6, the paper demo's value; 0 disables; "
                             "each iteration is a full model run of roughly 5-7 "
                             "minutes, and serious calibration wants more "
                             "repetitions than the 17 tunable parameters)")
    parser.add_argument("--dds-trials", type=int, default=1,
                        help="Independent seeded DDS trials run as PARALLEL "
                             "sibling jobs; a reducer picks the best result "
                             "(default: 1 = single sequential trajectory). "
                             "N trials use N worker slots but do not extend "
                             "wall time")
    parser.add_argument("--hydrofabric-tar", type=str, default=None,
                        help="Path to an existing hydrofabric cache tarball; "
                             "skips the shared fetch_hydrofabric job")
    parser.add_argument("--container-image", type=str, default=None,
                        help="Apptainer .sif path (relative to the workflow "
                             "directory) or a full container URI (default: "
                             "Apptainer/NextGen_Container.sif)")
    parser.add_argument("--teehr-image", type=str, default=None,
                        help="Apptainer .sif path or full container URI for "
                             "the teehr_evaluation job (default: "
                             "Apptainer/Teehr_Container.sif)")

    args = parser.parse_args()

    # --- Input validation, before any Pegasus API calls ---
    if args.gages_file:
        if not os.path.exists(args.gages_file):
            print(f"Error: gages file not found: {args.gages_file}")
            sys.exit(1)
        with open(args.gages_file) as fh:
            file_gages = [
                line.strip() for line in fh
                if line.strip() and not line.startswith("#")
            ]
        args.gages = (args.gages or []) + file_gages

    if not args.gages:
        print("Error: one of --gages or --gages-file is required")
        sys.exit(1)

    # Deduplicate while preserving order — a repeated gage would collide on job IDs.
    seen = set()
    deduped = []
    for gage in args.gages:
        if gage in seen:
            print(f"Warning: duplicate gage {gage} ignored")
            continue
        seen.add(gage)
        deduped.append(gage)
    args.gages = deduped

    for gage in args.gages:
        if "/" in gage or " " in gage:
            print(f"Error: invalid gage ID (no spaces or slashes): {gage!r}")
            sys.exit(1)

    if args.start >= args.end:
        print(f"Error: --start ({args.start}) must precede --end ({args.end})")
        sys.exit(1)

    if args.training_start and not (args.start <= args.training_start < args.end):
        print(
            f"Error: --training-start ({args.training_start}) must fall within "
            f"[{args.start}, {args.end})"
        )
        sys.exit(1)

    if args.hydrofabric_tar and not os.path.exists(args.hydrofabric_tar):
        print(f"Error: hydrofabric tarball not found: {args.hydrofabric_tar}")
        sys.exit(1)

    if args.calibrate < 0:
        print("Error: --calibrate must be >= 0")
        sys.exit(1)

    n_gages = len(args.gages)
    per_gage = 8 if not args.calibrate else 12
    est_jobs = per_gage * n_gages + 1 + (0 if args.hydrofabric_tar else 1)

    logger.info("=" * 70)
    logger.info("NEXTGEN (NGIAB) WORKFLOW GENERATOR")
    logger.info("=" * 70)
    logger.info(f"Gages ({n_gages}): {', '.join(args.gages)}")
    logger.info(f"Period: {args.start} to {args.end}")
    logger.info(
        f"Calibration: {'disabled' if not args.calibrate else f'{args.calibrate} DDS repetitions'}"
    )
    logger.info(
        "Hydrofabric: "
        + (f"reusing {args.hydrofabric_tar}" if args.hydrofabric_tar
           else "fetched by a shared job")
    )
    logger.info(f"Estimated jobs: ~{est_jobs}")
    logger.info(f"Execution site: {args.execution_site_name}")
    logger.info(
        f"Hosted site catalog: {args.hosted_site_catalog or '(none — supply your own site catalog)'}"
    )
    logger.info(f"Output file: {args.output}")
    logger.info("=" * 70)

    try:
        workflow = NextGenWorkflow(
            dagfile=args.output, container_image=args.container_image,
            teehr_image=args.teehr_image
        )

        workflow.create_pegasus_properties(
            hosted_site_catalog=args.hosted_site_catalog)
        workflow.calibrate_repetitions = args.calibrate
        workflow.create_transformation_catalog(
            exec_site_name=args.execution_site_name)
        workflow.create_replica_catalog(hydrofabric_tar=args.hydrofabric_tar)
        workflow.create_workflow(args)
        workflow.write()

        logger.info(f"\nWorkflow written to {args.output}")
        # --output-dir: no site catalog defines "local", so Pegasus's
        # built-in local site would otherwise stage outputs to ./wf-output.
        logger.info(
            f"Plan and submit: pegasus-plan --dir submit "
            f"-s {args.execution_site_name} -o local "
            f"--output-dir {workflow.local_storage_dir} --submit {args.output}"
        )

    except Exception as e:
        logger.error(f"Failed to generate workflow: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
