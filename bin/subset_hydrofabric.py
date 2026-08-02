#!/usr/bin/env python3

"""Subset the CONUS hydrofabric at a gage (notebook 1, `ngiab_data_cli -s`).

Unpacks the shared hydrofabric cache into a private HOME so no download is
needed, runs the subset, and exports the resulting config/ directory (which
holds {gage}_subset.gpkg) as a tarball for the downstream prep jobs.
"""

import argparse
import os
import sys

import ngiab_pegasus as np_util


def main():
    parser = argparse.ArgumentParser(description="Subset the hydrofabric at a gage")
    parser.add_argument("--gage", required=True, help="Gage ID, e.g. gage-10109001")
    parser.add_argument("--hydrofabric-cache", required=True,
                        help="Hydrofabric cache tarball from fetch_hydrofabric")
    parser.add_argument("--output", required=True, help="Output subset tarball")
    args = parser.parse_args()

    np_util.configure_logging()
    home = np_util.job_home()

    # Restore the cache so ngiab_data_cli finds the hydrofabric locally.
    np_util.extract_tar(args.hydrofabric_cache, home)
    cache_dir = os.path.join(home, ".ngiab")
    if not os.path.isdir(cache_dir):
        print(
            f"Error: hydrofabric cache tarball did not contain .ngiab/ "
            f"(looked in {home})",
            file=sys.stderr,
        )
        sys.exit(1)

    root = np_util.data_root(home)
    np_util.run_ngiab_cli(["-i", args.gage, "-s"])

    gage_path = os.path.join(root, args.gage)
    if not os.path.isdir(gage_path):
        print(
            f"Error: expected NGIAB output at {gage_path}; contents of {root}: "
            f"{os.listdir(root) if os.path.isdir(root) else 'missing'}",
            file=sys.stderr,
        )
        sys.exit(1)

    gpkg = np_util.find_gpkg(gage_path)
    print(f"Subset GeoPackage: {gpkg}")

    # Export only the config/ subtree, relative to the gage directory.
    np_util.create_tar(args.output, gage_path, ["config"])


if __name__ == "__main__":
    main()
