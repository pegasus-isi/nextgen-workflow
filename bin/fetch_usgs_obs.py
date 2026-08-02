#!/usr/bin/env python3

"""Fetch observed daily streamflow from USGS NWIS for one gage.

Extracted from notebooks 2 and 3, which each re-fetched observations inline. No
credentials are required. NWIS is a REQUIRED source for this workflow, so on
final failure the declared output is still written (empty) and the job exits
non-zero: exiting without the declared file would make HTCondor hold the job on
a stage-out error and hang the DAG.
"""

import argparse
import sys

import ngiab_pegasus as np_util


def gage_to_site(gage):
    """Turn a hydrofabric gage ID ('gage-10109001') into an NWIS site number."""
    return str(gage).split("-")[-1].strip()


def write_empty(path, reason):
    """Write the declared output with headers only, so stage-out succeeds."""
    with open(path, "w") as fh:
        fh.write("datetime,site_no,discharge_cfs\n")
    print(f"Wrote empty {path} ({reason})", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Fetch USGS NWIS streamflow")
    parser.add_argument("--gage", required=True, help="Gage ID, e.g. gage-10109001")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--output", required=True, help="Output CSV path")
    args = parser.parse_args()

    np_util.configure_logging()
    np_util.ensure_parent(args.output)
    site = gage_to_site(args.gage)
    print(f"Fetching NWIS daily values for site {site}: {args.start} to {args.end}")

    try:
        from dataretrieval import nwis
    except ImportError as exc:
        write_empty(args.output, f"dataretrieval unavailable: {exc}")
        sys.exit(1)

    def fetch():
        # 00060 = discharge, cubic feet per second.
        df, _meta = nwis.get_dv(
            sites=site, start=args.start, end=args.end, parameterCd="00060"
        )
        if df is None or df.empty:
            raise RuntimeError(f"NWIS returned no rows for site {site}")
        return df

    try:
        df = np_util.retry(fetch, attempts=4, base_delay=10, what="NWIS get_dv")
    except Exception as exc:  # noqa: BLE001 - report and fail loudly
        write_empty(args.output, f"NWIS fetch failed: {exc}")
        sys.exit(1)

    df = df.reset_index()

    # NWIS discharge column names vary by site and approval status.
    flow_col = None
    for candidate in ("00060_Mean", "00060", "00060_Mean_cd"):
        if candidate in df.columns:
            flow_col = candidate
            break
    if flow_col is None:
        numeric = [c for c in df.columns if c.startswith("00060")]
        if numeric:
            flow_col = numeric[0]
    if flow_col is None:
        write_empty(
            args.output,
            f"no discharge column in NWIS response; columns={list(df.columns)}",
        )
        sys.exit(1)

    time_col = "datetime" if "datetime" in df.columns else df.columns[0]
    out = df[[time_col, flow_col]].copy()
    out.columns = ["datetime", "discharge_cfs"]
    out.insert(1, "site_no", site)
    out.to_csv(args.output, index=False)

    print(
        f"Wrote {len(out)} daily observations to {args.output} "
        f"(column {flow_col})"
    )


if __name__ == "__main__":
    main()
