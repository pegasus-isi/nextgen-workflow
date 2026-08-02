#!/usr/bin/env python3

"""Apply calibrated parameters and re-run NextGen (notebook 5, final cell).

This is the second half of the unrolled calibration cycle: the calibrate job
found a best parameter set, this job writes it into the module configs and runs
the model once more so the calibrated run can be evaluated by the same analysis
jobs as the default-parameter run.
"""

import argparse
import os
import sys
from pathlib import Path

import ngiab_pegasus as np_util


def apply_parameters(gage_path, params):
    """Write calibrated parameters into realization.json.

    Mirrors NextGenSetup.write_config exactly: cal_utils.update_parameters()
    replaces the model_params of the CFE and NoahOWP modules inside the
    realization, which is where the engine reads them on the next run. The
    params argument is calibrate's best_params payload — per-module dicts
    keyed by config names ({"CFE": {...}, "NoahOWP": {...}}).
    """
    sys.path.insert(0, os.getcwd())
    from cal_utils import update_parameters

    realization = np_util.realization_path(gage_path)
    applied = {"cfe": 0, "noahowp": 0}
    for key, label in (("cfe", "CFE"), ("noahowp", "NoahOWP")):
        module_params = params.get(label)
        if module_params:
            update_parameters(str(realization), module_params, label)
            applied[key] = len(module_params)

    print(f"Applied {applied['cfe']} CFE and {applied['noahowp']} NoahOWP "
          f"parameters to {realization}")
    if applied["cfe"] == 0 and applied["noahowp"] == 0:
        raise RuntimeError(
            f"best_params carried no CFE or NoahOWP parameter map "
            f"(keys: {list(params.keys())}); calibrated parameters would "
            f"have no effect, so the re-run is pointless."
        )


def main():
    parser = argparse.ArgumentParser(
        description="Apply calibrated parameters and re-run NextGen"
    )
    parser.add_argument("--gage", required=True, help="Gage ID")
    parser.add_argument("--rundir-tar", required=True,
                        help="Assembled run-directory tarball (pre-run tree)")
    parser.add_argument("--params", required=True,
                        help="Best-parameters JSON from calibrate")
    parser.add_argument("--output", required=True,
                        help="Output calibrated run-directory tarball")
    parser.add_argument("--serial", action="store_true",
                        help="Run NextGen serially")
    args = parser.parse_args()

    try:
        _main(args)
    except SystemExit as exc:
        if exc.code not in (0, None):
            _ensure_declared_outputs(args)
        raise
    except Exception as exc:  # noqa: BLE001 - fail loud, not held
        print(f"apply_params failed: {exc}", file=sys.stderr)
        _ensure_declared_outputs(args)
        sys.exit(1)


def _ensure_declared_outputs(args):
    """Create an empty declared output before a failing exit.

    Exiting without it makes HTCondor hold the job on stage-out instead of
    letting the DAG see the failure (see CLAUDE.md workflow-generation
    gotchas).
    """
    if args.output and not os.path.exists(args.output):
        np_util.ensure_parent(args.output)
        with open(args.output, "wb"):
            pass


def _main(args):
    np_util.configure_logging()
    home = np_util.job_home()
    gage_path = np_util.gage_dir(args.gage, home)
    np_util.extract_tar(args.rundir_tar, gage_path)
    np_util.data_root(home)

    payload = np_util.load_json(args.params)
    if payload.get("baseline_retained"):
        # Calibration could not beat the untouched defaults, so calibrate
        # deliberately shipped no parameters. Re-run unchanged: downstream
        # evaluation then compares defaults against defaults instead of
        # installing a downgrade.
        print(
            "baseline_retained is set (calibration did not beat the default "
            f"parameterization: baseline "
            f"{payload.get('baseline_objective_value')} vs sampled best "
            f"{payload.get('sampled_best_objective_value')}); applying no "
            "parameter changes"
        )
    else:
        params = payload.get("best_params", payload)
        if not params:
            raise RuntimeError(f"{args.params} contains no best_params")
        print(f"Calibrated parameters: {params}")
        apply_parameters(gage_path, params)

    try:
        from pyngiab import PyNGIAB
    except ImportError as exc:
        print(f"Error: pyngiab not importable ({exc})", file=sys.stderr)
        sys.exit(1)

    print(f"Re-running NextGen with calibrated parameters in {gage_path}")
    PyNGIAB(gage_path, serial_execution_mode=bool(args.serial)).run()

    troute_files = sorted(Path(gage_path, "outputs", "troute").glob("*.nc"))
    if not troute_files:
        print(
            f"Error: calibrated re-run produced no t-route NetCDF in "
            f"{gage_path}/outputs/troute",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"t-route NetCDF files: {[f.name for f in troute_files]}")

    np_util.create_tar(args.output, gage_path, ["config", "forcings", "outputs"])


if __name__ == "__main__":
    main()
