#!/usr/bin/env python3

"""Run NextGen + t-route for one gage (notebook 2).

Unpacks the assembled run directory, runs the model through PyNGIAB, plots the
simulated hydrograph against the USGS observations, and exports the populated
run directory as a new tarball.
"""

import argparse
import os
import sys
from pathlib import Path

import ngiab_pegasus as np_util


def plot_hydrograph(gage, gage_path, obs_csv, plot_path, feature_id):
    """Plot simulated vs observed streamflow. Never fails the job."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import pandas as pd

        sys.path.insert(0, os.getcwd())
        from ngen_outputs_utils import get_flow_data_from_netcdf

        troute_files = sorted(Path(gage_path, "outputs", "troute").glob("*.nc"))
        if not troute_files:
            print("No t-route NetCDF found; skipping hydrograph", file=sys.stderr)
            return

        sim_m3_per_hour = get_flow_data_from_netcdf(
            str(troute_files[0]), feature_id
        )

        fig, ax = plt.subplots(figsize=(11, 4.5))
        # m3/h -> cfs so both series share units with the NWIS observations.
        sim_cfs = [v / 3600.0 * 35.3147 for v in sim_m3_per_hour]
        ax.plot(range(len(sim_cfs)), sim_cfs, lw=0.8,
                label=f"NextGen simulated (feature {feature_id})")

        obs = pd.read_csv(obs_csv)
        if not obs.empty and "discharge_cfs" in obs.columns:
            ax2 = ax.twiny()
            ax2.plot(range(len(obs)), obs["discharge_cfs"], color="black",
                     lw=0.8, alpha=0.7, label="USGS observed")
            ax2.set_xticks([])
            ax2.legend(loc="upper right")

        ax.set_xlabel("Model time step (hours)")
        ax.set_ylabel("Streamflow (cfs)")
        ax.set_title(f"{gage}: NextGen simulated vs USGS observed streamflow")
        ax.legend(loc="upper left")
        fig.tight_layout()

        np_util.ensure_parent(plot_path)
        fig.savefig(plot_path, dpi=120)
        plt.close(fig)
        print(f"Wrote {plot_path}")
    except Exception as exc:  # noqa: BLE001 - a plot must not fail the model run
        print(f"Hydrograph plot skipped: {exc}", file=sys.stderr)
        # Still create the declared output so stage-out succeeds.
        _placeholder_plot(plot_path, f"{gage}: plot unavailable\n{exc}")


def _placeholder_plot(plot_path, message):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(8, 3))
        ax.axis("off")
        ax.text(0.5, 0.5, message, ha="center", va="center", wrap=True)
        np_util.ensure_parent(plot_path)
        fig.savefig(plot_path, dpi=100)
        plt.close(fig)
    except Exception:  # noqa: BLE001
        # Last resort: an empty file still satisfies stage-out.
        np_util.ensure_parent(plot_path)
        open(plot_path, "wb").close()


def _ensure_declared_outputs(args):
    """Create empty declared outputs before a failing exit.

    If the job exits without them, HTCondor holds it on stage-out instead of
    letting the DAG see the failure (see CLAUDE.md workflow-generation
    gotchas). The plot gets a placeholder image rather than a zero-byte file.
    """
    if args.output and not os.path.exists(args.output):
        np_util.ensure_parent(args.output)
        with open(args.output, "wb"):
            pass
    if args.plot and not os.path.exists(args.plot):
        _placeholder_plot(args.plot, f"{args.gage}: run_nextgen failed")


def main():
    parser = argparse.ArgumentParser(description="Run NextGen + t-route")
    parser.add_argument("--gage", required=True, help="Gage ID")
    parser.add_argument("--rundir-tar", required=True,
                        help="Assembled run-directory tarball")
    parser.add_argument("--obs", required=True, help="USGS observations CSV")
    parser.add_argument("--output", required=True,
                        help="Output run-directory tarball (with model outputs)")
    parser.add_argument("--plot", required=True, help="Output hydrograph PNG")
    parser.add_argument("--serial", action="store_true",
                        help="Run NextGen serially instead of multiprocessing "
                             "across catchments")
    args = parser.parse_args()

    try:
        _main(args)
    except SystemExit as exc:
        if exc.code not in (0, None):
            _ensure_declared_outputs(args)
        raise
    except Exception as exc:  # noqa: BLE001 - fail loud, not held
        print(f"run_nextgen failed: {exc}", file=sys.stderr)
        _ensure_declared_outputs(args)
        sys.exit(1)


def _main(args):
    np_util.configure_logging()
    home = np_util.job_home()
    gage_path = np_util.gage_dir(args.gage, home)

    np_util.extract_tar(args.rundir_tar, gage_path)
    np_util.find_gpkg(gage_path)
    realization = np_util.realization_path(gage_path)
    if not os.path.exists(realization):
        print(f"Error: no realization at {realization}", file=sys.stderr)
        sys.exit(1)

    feature_id = np_util.routing_feature_id(gage_path)
    print(f"Routing feature id for {args.gage}: {feature_id}")

    try:
        from pyngiab import PyNGIAB
    except ImportError as exc:
        print(
            f"Error: pyngiab is not importable in this environment ({exc}). "
            f"The wrapper runs inside the NGIAB container, which provides it.",
            file=sys.stderr,
        )
        sys.exit(1)

    def _troute_files():
        troute_dir = os.path.join(gage_path, "outputs", "troute")
        if not os.path.isdir(troute_dir):
            return []
        return sorted(Path(troute_dir).glob("*.nc"))

    print(f"Running NextGen in {gage_path} "
          f"(serial_execution_mode={bool(args.serial)})")
    runner = PyNGIAB(gage_path, serial_execution_mode=bool(args.serial))
    runner.run()

    troute_files = _troute_files()
    if not troute_files and not args.serial:
        # PyNGIAB's parallel mode partitions across all cores; a basin with
        # fewer catchments than cores makes partitionGenerator abort (status
        # -6) and PyNGIAB swallows the error rather than raising. Detect the
        # missing routing output and retry serially before giving up.
        print(
            "Parallel NextGen produced no routing output (small basins can "
            "have fewer catchments than partitions); retrying serially",
            file=sys.stderr,
        )
        PyNGIAB(gage_path, serial_execution_mode=True).run()
        troute_files = _troute_files()
    ngen_dir = os.path.join(gage_path, "outputs", "ngen")
    n_cat = len(os.listdir(ngen_dir)) if os.path.isdir(ngen_dir) else 0
    print(f"NextGen produced {n_cat} catchment output files")
    print(f"t-route NetCDF files: {[f.name for f in troute_files]}")

    if not troute_files:
        print(
            f"Error: NextGen finished but produced no t-route NetCDF in "
            f"{troute_dir}. Downstream evaluation has nothing to read.",
            file=sys.stderr,
        )
        sys.exit(1)

    plot_hydrograph(args.gage, gage_path, args.obs, args.plot, feature_id)

    np_util.create_tar(args.output, gage_path, ["config", "forcings", "outputs"])


if __name__ == "__main__":
    main()
