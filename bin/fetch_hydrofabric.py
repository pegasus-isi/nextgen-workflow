#!/usr/bin/env python3

"""Fetch the CONUS hydrofabric once and export it as a reusable cache tarball.

NGIAB downloads the national hydrofabric (several GB) into $HOME/.ngiab the
first time a subset is requested. Doing that once per gage would waste bandwidth
and time, so this job warms the cache in a private HOME and tars it. Every
subset_hydrofabric job then unpacks this tarball instead of downloading.

The cache is warmed by asking for a real subset of the first gage; the subset
output itself is discarded, only the cache is exported.
"""

import argparse
import os
import sys

import ngiab_pegasus as np_util


def main():
    parser = argparse.ArgumentParser(
        description="Download the CONUS hydrofabric and export it as a tarball"
    )
    parser.add_argument("--gage", required=True,
                        help="Gage ID used to trigger the download, e.g. gage-10109001")
    parser.add_argument("--output", required=True,
                        help="Output cache tarball path")
    args = parser.parse_args()

    np_util.configure_logging()
    home = np_util.job_home()
    np_util.data_root(home)

    print(f"Warming the hydrofabric cache in {home} using {args.gage}")
    np_util.retry(
        lambda: np_util.run_ngiab_cli(["-i", args.gage, "-s"]),
        attempts=3,
        base_delay=15,
        what="hydrofabric download",
    )

    cache_dir = os.path.join(home, ".ngiab")
    if not os.path.isdir(cache_dir):
        print(
            f"Error: expected the hydrofabric cache at {cache_dir} but it does "
            f"not exist. The NGIAB cache location may differ in this image; "
            f"check `ls -a {home}` in the job's scratch directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    np_util.create_tar(args.output, home, [".ngiab"])

    size_gb = os.path.getsize(args.output) / (1024 ** 3)
    print(f"Hydrofabric cache: {args.output} ({size_gb:.2f} GB)")


if __name__ == "__main__":
    main()
