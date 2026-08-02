#!/usr/bin/env python3

"""Subset and regrid AORC forcings (notebook 1, `ngiab_data_cli -f`).

Rebuilds the gage directory from the subset tarball, runs the forcings step for
the requested period, and exports the forcings/ subtree.
"""

import argparse
import os
import sys

import ngiab_pegasus as np_util


def main():
    parser = argparse.ArgumentParser(description="Generate AORC forcings for a gage")
    parser.add_argument("--gage", required=True, help="Gage ID")
    parser.add_argument("--subset-tar", required=True,
                        help="Subset tarball from subset_hydrofabric")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--output", required=True, help="Output forcings tarball")
    args = parser.parse_args()

    np_util.configure_logging()
    home = np_util.job_home()
    gage_path = np_util.gage_dir(args.gage, home)

    np_util.extract_tar(args.subset_tar, gage_path)
    np_util.find_gpkg(gage_path)  # fail early if the subset is not usable

    # AORC subsetting pulls from public cloud storage; retry transient failures.
    np_util.retry(
        lambda: np_util.run_ngiab_cli(
            ["-i", args.gage, "-f", "--start", args.start, "--end", args.end]
        ),
        attempts=3,
        base_delay=15,
        what="AORC forcings subset",
    )

    forcings_dir = os.path.join(gage_path, "forcings")
    if not os.path.isdir(forcings_dir):
        print(
            f"Error: expected forcings at {forcings_dir}; gage directory holds "
            f"{os.listdir(gage_path)}",
            file=sys.stderr,
        )
        sys.exit(1)

    produced = sorted(os.listdir(forcings_dir))
    print(f"Forcings files: {produced}")
    if not produced:
        print(f"Error: {forcings_dir} is empty", file=sys.stderr)
        sys.exit(1)

    np_util.create_tar(args.output, gage_path, ["forcings"])


if __name__ == "__main__":
    main()
