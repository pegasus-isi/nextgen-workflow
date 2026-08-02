#!/usr/bin/env python3

"""Generate realization.json + module configs and patch in the output variables.

Combines two steps from notebook 1: `ngiab_data_cli -r`, then the cell that
inserts the 27 model output variables into the bmi_multi formulation (without
which the downstream analysis has nothing to aggregate).

Exports the whole config/ subtree, so the assembled run directory picks up the
realization, the module configs, and the GeoPackage together.
"""

import argparse
import os
import sys

import ngiab_pegasus as np_util

# The 27 output variables the paper's notebook adds to the realization, verbatim.
OUTPUT_VARS = [
    "RAIN_RATE", "GIUH_RUNOFF", "DIRECT_RUNOFF", "NASH_LATERAL_RUNOFF",
    "DEEP_GW_TO_CHANNEL_FLUX", "SOIL_TO_GW_FLUX", "Q_OUT", "POTENTIAL_ET",
    "ACTUAL_ET", "GW_STORAGE", "SOIL_STORAGE", "SOIL_STORAGE_CHANGE",
    "SURF_RUNOFF_SCHEME", "NWM_PONDED_DEPTH", "QINSUR", "SNEQV", "SNOWH",
    "QSNOW", "ACSNOM", "ECAN", "ETRAN", "QSEVA", "EVAPOTRANS", "QRAIN",
    "CMC", "SNLIQ", "FSNO",
]


def patch_output_variables(realization_file):
    """Insert output_variables before 'modules' in each bmi_multi formulation."""
    realization = np_util.load_json(realization_file)

    patched = 0
    for form in realization.get("global", {}).get("formulations", []):
        if form.get("name") != "bmi_multi":
            continue
        params = form.get("params", {})
        if "modules" in params:
            # Preserve ordering: output_variables immediately before modules.
            new_params = {}
            for key, value in params.items():
                if key == "modules":
                    new_params["output_variables"] = OUTPUT_VARS
                new_params[key] = value
            form["params"] = new_params
        else:
            params["output_variables"] = OUTPUT_VARS
        patched += 1

    if patched == 0:
        raise RuntimeError(
            f"No bmi_multi formulation found in {realization_file}; cannot add "
            f"output variables. Realization keys: {list(realization.keys())}"
        )

    np_util.dump_json(realization, realization_file)
    print(f"Inserted {len(OUTPUT_VARS)} output_variables into {patched} formulation(s)")


def main():
    parser = argparse.ArgumentParser(
        description="Generate and patch the NextGen realization for a gage"
    )
    parser.add_argument("--gage", required=True, help="Gage ID")
    parser.add_argument("--subset-tar", required=True,
                        help="Subset tarball from subset_hydrofabric")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--forcings-tar", default=None,
                        help="Optional forcings tarball. Only needed if a real "
                             "run shows that `ngiab_data_cli -r` requires "
                             "forcings/forcings.nc to exist.")
    parser.add_argument("--output", required=True, help="Output realization tarball")
    args = parser.parse_args()

    np_util.configure_logging()
    home = np_util.job_home()
    gage_path = np_util.gage_dir(args.gage, home)

    np_util.extract_tar(args.subset_tar, gage_path)
    if args.forcings_tar:
        np_util.extract_tar(args.forcings_tar, gage_path)
    np_util.find_gpkg(gage_path)

    np_util.run_ngiab_cli(
        ["-i", args.gage, "-r", "--start", args.start, "--end", args.end]
    )

    realization_file = np_util.realization_path(gage_path)
    if not os.path.exists(realization_file):
        config_dir = os.path.join(gage_path, "config")
        print(
            f"Error: {realization_file} was not created. config/ holds "
            f"{os.listdir(config_dir) if os.path.isdir(config_dir) else 'nothing'}",
            file=sys.stderr,
        )
        sys.exit(1)

    patch_output_variables(realization_file)
    np_util.create_tar(args.output, gage_path, ["config"])


if __name__ == "__main__":
    main()
