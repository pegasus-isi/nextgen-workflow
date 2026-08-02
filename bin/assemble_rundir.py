#!/usr/bin/env python3

"""Merge the three prep outputs into one NGIAB run directory.

The prep branches emit loose subtrees (config/ from the subset and realization
steps, forcings/ from the forcings step). NextGen expects them together in one
directory alongside empty outputs/ subdirectories. This job builds that layout
and exports it as a single tarball, so every later job consumes exactly one
tarball and emits a new one instead of mutating a tree in place.
"""

import argparse
import os

import ngiab_pegasus as np_util


def main():
    parser = argparse.ArgumentParser(
        description="Assemble an NGIAB run directory from the prep outputs"
    )
    parser.add_argument("--gage", required=True, help="Gage ID")
    parser.add_argument("--subset-tar", required=True, help="Subset tarball")
    parser.add_argument("--forcings-tar", required=True, help="Forcings tarball")
    parser.add_argument("--realization-tar", required=True,
                        help="Realization tarball")
    parser.add_argument("--output", required=True, help="Output run-dir tarball")
    args = parser.parse_args()

    np_util.configure_logging()
    home = np_util.job_home()
    gage_path = np_util.gage_dir(args.gage, home)

    # Order matters: the realization tarball also carries config/, and its
    # realization.json (with output_variables patched in) must win.
    np_util.extract_tar(args.subset_tar, gage_path)
    np_util.extract_tar(args.forcings_tar, gage_path)
    np_util.extract_tar(args.realization_tar, gage_path)

    # NextGen writes here; create them so the model does not have to.
    for sub in ("outputs", "outputs/ngen", "outputs/troute"):
        np_util.ensure_dir(os.path.join(gage_path, sub))

    gpkg = np_util.find_gpkg(gage_path)
    realization = np_util.realization_path(gage_path)
    if not os.path.exists(realization):
        raise FileNotFoundError(f"Assembled run directory has no {realization}")

    # Fail here rather than inside the model run if the realization lost its
    # output variables somewhere in the merge.
    parsed = np_util.load_json(realization)
    n_vars = 0
    for form in parsed.get("global", {}).get("formulations", []):
        n_vars = max(n_vars, len(form.get("params", {}).get("output_variables", [])))
    if n_vars == 0:
        raise RuntimeError(
            f"{realization} has no output_variables after assembly - the "
            f"realization tarball may have been overwritten by the subset one"
        )

    forcings_dir = os.path.join(gage_path, "forcings")
    print(f"GeoPackage:  {os.path.relpath(gpkg, gage_path)}")
    print(f"Realization: output_variables={n_vars}")
    print(f"Forcings:    {sorted(os.listdir(forcings_dir))}")

    np_util.create_tar(
        args.output, gage_path, ["config", "forcings", "outputs"]
    )


if __name__ == "__main__":
    main()
