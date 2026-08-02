#!/usr/bin/env python3

"""Pick the winning parameter set from parallel DDS calibration trials.

A single DDS trajectory is inherently sequential, so the workflow
parallelizes calibration as N independent seeded trials (sibling calibrate
jobs). This reducer compares their best objective values, forwards the
winner's best-parameters JSON to apply_params, and merges the per-trial
iteration logs into one CSV with a `trial` column.
"""

import argparse
import os
import sys

import ngiab_pegasus as np_util

# Objective functions where a larger value is better. cal_utils supports
# KGE (paper default) and RMSE.
HIGHER_IS_BETTER = {"KGE", "NSE", "CORRELATION"}


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
            fh.write("trial,iteration,objective_value\n")


def main():
    parser = argparse.ArgumentParser(
        description="Select the best parallel calibration trial"
    )
    parser.add_argument("--gage", required=True, help="Gage ID")
    parser.add_argument("--trial-params", required=True, nargs="+",
                        help="Best-parameters JSONs, one per trial")
    parser.add_argument("--trial-iterations", required=True, nargs="+",
                        help="Iteration-log CSVs, one per trial")
    parser.add_argument("--output-params", required=True,
                        help="Winning best-parameters JSON")
    parser.add_argument("--output-iterations", required=True,
                        help="Merged iteration log with a trial column")
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
        print(f"select_best_params failed: {exc}", file=sys.stderr)
        _ensure_declared_outputs(args)
        sys.exit(1)


def _main(args):
    np_util.configure_logging()
    sys.path.insert(0, os.getcwd())

    candidates = []
    for path in args.trial_params:
        try:
            payload = np_util.load_json(path)
        except Exception as exc:  # noqa: BLE001
            print(f"Skipping unreadable trial payload {path}: {exc}",
                  file=sys.stderr)
            continue
        # best_objective_value is the RAW metric (KGE/NSE/RMSE), never the
        # DDS-internal transformed score — calibrate.py guarantees that. A
        # baseline_retained payload legitimately carries empty best_params
        # (the defaults won); a failed trial writes an empty payload
        # entirely (fail-loud contract) and is skipped — multi-start
        # survives as long as one trial produced a result.
        value = payload.get("best_objective_value")
        usable = payload.get("best_params") or payload.get("baseline_retained")
        if not usable or value is None:
            print(f"Skipping trial without usable result: {path}",
                  file=sys.stderr)
            continue
        candidates.append((float(value), path, payload))

    if not candidates:
        raise RuntimeError(
            f"None of the {len(args.trial_params)} calibration trials "
            f"produced a usable best_params payload"
        )

    objective = str(candidates[0][2].get("objective_function", "kge")).upper()
    pick_max = objective in HIGHER_IS_BETTER
    candidates.sort(key=lambda c: c[0], reverse=pick_max)

    best_value, best_path, winner = candidates[0]
    winner["selected_from_trials"] = len(args.trial_params)
    winner["trial_objectives"] = {
        os.path.basename(path): value for value, path, _ in candidates
    }
    print(f"Objective {objective} ({'max' if pick_max else 'min'} wins); "
          f"selected {os.path.basename(best_path)} "
          f"(seed={winner.get('seed')}) at {best_value}")

    np_util.dump_json(winner, args.output_params)
    print(f"Wrote {args.output_params}")

    _merge_iterations(args.trial_iterations, args.output_iterations)


def _merge_iterations(trial_csvs, output_path):
    """Concatenate per-trial iteration logs, tagged by trial index."""
    import pandas as pd

    frames = []
    for idx, path in enumerate(trial_csvs, start=1):
        try:
            frame = pd.read_csv(path)
        except Exception as exc:  # noqa: BLE001
            print(f"Skipping unreadable iteration log {path}: {exc}",
                  file=sys.stderr)
            continue
        frame.insert(0, "trial", idx)
        frames.append(frame)

    np_util.ensure_parent(output_path)
    if frames:
        pd.concat(frames, ignore_index=True).to_csv(output_path, index=False)
    else:
        with open(output_path, "w") as fh:
            fh.write("trial,iteration,objective_value\n")
    print(f"Wrote {output_path} ({sum(len(f) for f in frames)} rows from "
          f"{len(frames)} trial(s))")


if __name__ == "__main__":
    main()
