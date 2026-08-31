#!/usr/bin/env python3

"""SPOTPY DDS calibration of CFE and NoahOWP parameters (notebook 5).

Each DDS iteration rewrites the module configs and re-runs the whole NextGen
model, so this job is expensive by construction: roughly 5-7 minutes per
iteration. The workflow generator enables it by default with the paper demo's
6 repetitions (--calibrate 0 disables; serious calibration wants more
repetitions than the 17 tunable parameters).

Delegates to run_spotpy() from the paper's own cal_utils module (vendored under
bin/lib/) so the optimisation and objective function match the publication.
"""

import argparse
import os
import sys

import ngiab_pegasus as np_util


def _ensure_declared_outputs(args):
    """Create empty declared outputs before a failing exit.

    Exiting without them makes HTCondor hold the job on stage-out instead of
    letting the DAG see the failure (see CLAUDE.md workflow-generation
    gotchas).
    """
    if args.output_params and not os.path.exists(args.output_params):
        np_util.ensure_parent(args.output_params)
        with open(args.output_params, "w") as fh:
            fh.write("{}\n")
    if args.output_iterations and not os.path.exists(args.output_iterations):
        np_util.ensure_parent(args.output_iterations)
        with open(args.output_iterations, "w") as fh:
            fh.write("iteration,objective_value\n")


def _observed_flow_pickle(gage, start, end):
    """Build the hourly observed-flow pickle cal_utils expects.

    NextGenSetup does pd.read_pickle() on a [Time, values] DataFrame of
    HOURLY flow in m^3/s, which the paper builds from USGS instantaneous
    (iv) data via cal_utils.process_usgs_streamflow(). The workflow's staged
    observations CSV is daily cfs — wrong service, wrong units, wrong
    format — so fetch the hourly record here, exactly the paper's way.
    """
    from cal_utils import process_usgs_streamflow

    site = str(gage).split("-")[-1]
    pkl_path = os.path.join(os.getcwd(), f"{gage}_obs_hourly.pkl")
    np_util.retry(
        lambda: process_usgs_streamflow(site, start, end, output_path=pkl_path),
        attempts=4,
        base_delay=10,
        what="NWIS instantaneous values fetch",
    )
    if not os.path.exists(pkl_path):
        raise RuntimeError(f"process_usgs_streamflow wrote no pickle at {pkl_path}")
    return pkl_path


def main():
    parser = argparse.ArgumentParser(
        description="Calibrate NextGen parameters with SPOTPY DDS"
    )
    parser.add_argument("--gage", required=True, help="Gage ID")
    parser.add_argument("--rundir-tar", required=True,
                        help="Run-directory tarball from the baseline "
                             "run_nextgen job")
    parser.add_argument("--obs", required=True, help="USGS observations CSV")
    parser.add_argument("--start", required=True, help="Simulation start YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Simulation end YYYY-MM-DD")
    parser.add_argument("--training-start", required=True,
                        help="Calibration period start YYYY-MM-DD")
    parser.add_argument("--repetitions", type=int, default=6,
                        help="SPOTPY repetitions (each is a full model run)")
    parser.add_argument("--dds-trials", type=int, default=1,
                        help="DDS trials (sequential, inside this job)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed; distinct seeds give independent "
                             "DDS trajectories for parallel multi-start "
                             "calibration")
    parser.add_argument("--algorithm", default="dds", help="SPOTPY algorithm")
    parser.add_argument("--objective-function", default="kge",
                        help="Objective function")
    parser.add_argument("--output-params", required=True,
                        help="Output best-parameters JSON")
    parser.add_argument("--output-iterations", required=True,
                        help="Output calibration iterations CSV")
    args = parser.parse_args()

    try:
        _main(args)
    except SystemExit as exc:
        if exc.code not in (0, None):
            _ensure_declared_outputs(args)
        raise
    except Exception as exc:  # noqa: BLE001 - fail loud, not held
        import traceback
        traceback.print_exc()
        print(f"calibrate failed: {exc}", file=sys.stderr)
        _ensure_declared_outputs(args)
        sys.exit(1)


def _main(args):
    np_util.configure_logging()
    home = np_util.job_home()
    gage_path = np_util.gage_dir(args.gage, home)
    np_util.extract_tar(args.rundir_tar, gage_path)
    np_util.data_root(home)
    sys.path.insert(0, os.getcwd())

    feature_id = np_util.routing_feature_id(gage_path)
    realization = np_util.realization_path(gage_path)

    from cal_utils import get_troute_output_name, run_spotpy

    troute_output_path = os.path.join(
        gage_path, "outputs", "troute", get_troute_output_name(realization)
    )
    print(f"Calibrating {args.gage}: feature_id={feature_id}, "
          f"repetitions={args.repetitions}, trials={args.dds_trials}")
    print(f"t-route output path: {troute_output_path}")

    # cal_utils identifies the site by bare USGS number, not the hydrofabric ID.
    gage_id = str(args.gage).split("-")[-1]

    if args.seed is not None:
        # SPOTPY's DDS draws from numpy's global RNG; seeding it makes each
        # parallel trial an independent, reproducible trajectory.
        import random

        import numpy as np

        np.random.seed(args.seed)
        random.seed(args.seed)
        print(f"Seeded RNGs with {args.seed}")

    # cal_utils needs an hourly m^3/s pickle, not the staged daily CSV
    # (args.obs is still declared so the DAG shape and provenance keep the
    # observations input visible).
    observed_pkl = _observed_flow_pickle(args.gage, args.start, args.end)

    baseline = _baseline_objective(
        gage_path, troute_output_path, observed_pkl,
        args.start, args.end, args.training_start, feature_id,
        args.objective_function,
    )

    best_params = run_spotpy(
        gage_id,
        args.start,
        args.end,
        args.training_start,
        observed_pkl,
        troute_output_path,
        gage_path,
        feature_id,
        # cal_utils string-compares these against uppercase literals; the
        # argparse defaults are lowercase.
        str(args.algorithm).upper(),
        str(args.objective_function).upper(),
        repetitions=args.repetitions,
        dds_trials=args.dds_trials,
    )

    if best_params is None:
        raise RuntimeError("run_spotpy() returned no parameter set")

    # run_spotpy returns the best parameter VECTOR, positionally ordered the
    # way NextGenSetup.write_config consumes it. Convert to the same
    # per-module config-key dicts write_config builds, so apply_params can
    # hand them straight to cal_utils.update_parameters().
    cfe_keys = ["b", "satpsi", "satdk", "maxsmc", "refkdt", "expon", "slope",
                "max_gw_storage", "Kn", "Klf", "Cgw"]
    noah_keys = ["MFSNO", "MP", "RSURF_EXP", "CWP", "VCMX25", "RSURF_SNOW",
                 "SCAMAX"]
    values = [float(v) for v in best_params]
    if len(values) != len(cfe_keys) + len(noah_keys):
        raise RuntimeError(
            f"expected {len(cfe_keys) + len(noah_keys)} calibrated values "
            f"(write_config's positional order), got {len(values)}"
        )
    best_by_module = {
        "CFE": dict(zip(cfe_keys, values)),
        "NoahOWP": dict(zip(noah_keys, values[len(cfe_keys):])),
    }

    # cal_utils writes calibration/iterations/calibration_iterations.csv inside
    # the run directory; copy it to the declared output location.
    export_iterations(gage_path, args.output_iterations)

    sampled_best = _best_objective(args.output_iterations,
                                   args.objective_function)

    # The baseline is a candidate: never install sampled parameters that
    # lose to the untouched defaults (run0006's DDS best of 0.610 would have
    # replaced a 0.860 default without this).
    higher_better = str(args.objective_function).upper() != "RMSE"
    keep_baseline = (
        baseline is not None
        and sampled_best is not None
        and (baseline >= sampled_best if higher_better
             else baseline <= sampled_best)
    )
    if keep_baseline:
        print(
            f"Calibration did NOT beat the default parameterization "
            f"(baseline {baseline} vs sampled best {sampled_best}); "
            f"retaining defaults"
        )

    payload = {
        "gage": args.gage,
        "feature_id": feature_id,
        "algorithm": args.algorithm,
        "objective_function": args.objective_function,
        "repetitions": args.repetitions,
        "dds_trials": args.dds_trials,
        "seed": args.seed,
        "training_start": args.training_start,
        "baseline_objective_value": baseline,
        "baseline_retained": keep_baseline,
        "best_objective_value": baseline if keep_baseline else sampled_best,
        "sampled_best_objective_value": sampled_best,
        "sampled_best_params": best_by_module,
        "best_params": {} if keep_baseline else best_by_module,
    }
    np_util.dump_json(payload, args.output_params)
    print(f"Wrote {args.output_params}")


def _best_objective(iterations_csv, objective_function):
    """Return the is_best row's RAW metric (KGE/RMSE/NSE/MAE column).

    Not objective_value: that column is the DDS-internal score (KGE-1, or
    -RMSE), which misled the first paper comparison and would break
    cross-trial selection. The raw metric columns sit alongside it.
    """
    try:
        import pandas as pd

        df = pd.read_csv(iterations_csv)
        best = df[df["is_best"] == True]  # noqa: E712 - CSV round-trips bools
        col = str(objective_function).upper()
        if col not in df.columns:
            col = "objective_value"
        return float(best.iloc[0][col])
    except Exception as exc:  # noqa: BLE001
        print(f"Could not extract best objective from {iterations_csv}: {exc}",
              file=sys.stderr)
        return None


def _baseline_objective(gage_path, troute_output_path, observed_pkl,
                        start, end, training_start, feature_id,
                        objective_function):
    """Score the untouched default parameterization with the same objective.

    The vendored calibration loop only ever selects among SAMPLED parameter
    vectors — the unmodified configuration is never a candidate, so DDS can
    (and did, in run0006: default 0.860 vs calibrated 0.612) install a
    downgrade. One extra model run here makes the baseline a candidate.
    """
    import spotpy

    from cal_utils import NextGenSetup

    model = NextGenSetup(
        str(gage_path).split("-")[-1], start, end, training_start,
        observed_pkl, troute_output_path, gage_path,
    )
    print("Scoring the untouched default parameterization (baseline run)")
    model.run_model(gage_path)
    simulated = model.evaluate(feature_id)
    observed = model.observed.values.squeeze()
    if str(objective_function).upper() == "RMSE":
        value = float(spotpy.objectivefunctions.rmse(observed, simulated))
    else:
        value = float(spotpy.objectivefunctions.kge(observed, simulated))
    print(f"Baseline {str(objective_function).upper()}: {value}")
    return value


def export_iterations(gage_path, output_path):
    """Locate the SPOTPY iteration log and copy it to the declared output."""
    import shutil

    candidates = [
        os.path.join(gage_path, "calibration", "iterations",
                     "calibration_iterations.csv"),
        os.path.join(gage_path, "calibration", "calibration_iterations.csv"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            np_util.ensure_parent(output_path)
            shutil.copyfile(candidate, output_path)
            print(f"Wrote {output_path} (from {candidate})")
            return

    # Search harder before giving up.
    for root, _dirs, files in os.walk(gage_path):
        for name in files:
            if name.endswith("calibration_iterations.csv"):
                np_util.ensure_parent(output_path)
                shutil.copyfile(os.path.join(root, name), output_path)
                print(f"Wrote {output_path} (from {root}/{name})")
                return

    # The parameters themselves are the primary output; an empty iteration log
    # should not fail the job, but say so loudly.
    print(
        f"Warning: no calibration_iterations.csv found under {gage_path}; "
        f"writing an empty {output_path}",
        file=sys.stderr,
    )
    np_util.ensure_parent(output_path)
    with open(output_path, "w") as fh:
        fh.write("iteration,objective_value\n")


if __name__ == "__main__":
    main()
