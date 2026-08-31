#!/usr/bin/env python3

"""Fetch observed hourly streamflow from USGS NWIS for one gage.

Extracted from notebooks 2 and 3, which each re-fetched observations inline.
The paper evaluates at hourly resolution (AUTHOR_REVIEW.md #2), so the primary
source is the instantaneous-values service resampled to hourly means; when a
site has no instantaneous record for the period, the job degrades to the
daily-values service and says so — downstream scoring detects the resolution
from the timestamps. No credentials are required. NWIS is a REQUIRED source
for this workflow, so on final failure the declared output is still written
(empty) and the job exits non-zero: exiting without the declared file would
make HTCondor hold the job on a stage-out error and hang the DAG.
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


def discharge_column(df):
    """Pick the numeric discharge column; NWIS names vary by site/approval."""
    for candidate in ("00060", "00060_Mean"):
        if candidate in df.columns:
            return candidate
    numeric = [c for c in df.columns
               if c.startswith("00060") and not c.endswith("_cd")]
    return numeric[0] if numeric else None


def fetch_hourly(nwis, site, start, end):
    """Instantaneous values resampled to hourly means, UTC-naive index."""
    df, _meta = nwis.get_iv(
        sites=site, start=start, end=end, parameterCd="00060"
    )
    if df is None or df.empty:
        raise RuntimeError(f"NWIS returned no instantaneous rows for {site}")
    col = discharge_column(df)
    if col is None:
        raise RuntimeError(
            f"no discharge column in NWIS iv response; columns={list(df.columns)}"
        )
    series = df[col]
    if series.index.tz is not None:
        series = series.tz_convert("UTC").tz_localize(None)
    return series.resample("1h").mean().dropna()


def fetch_daily(nwis, site, start, end):
    """Daily values — the degraded fallback for sites without iv records."""
    df, _meta = nwis.get_dv(
        sites=site, start=start, end=end, parameterCd="00060"
    )
    if df is None or df.empty:
        raise RuntimeError(f"NWIS returned no daily rows for site {site}")
    col = discharge_column(df)
    if col is None:
        raise RuntimeError(
            f"no discharge column in NWIS dv response; columns={list(df.columns)}"
        )
    series = df[col]
    if series.index.tz is not None:
        series = series.tz_convert("UTC").tz_localize(None)
    return series.dropna()


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
    print(f"Fetching NWIS hourly values for site {site}: {args.start} to {args.end}")

    try:
        from dataretrieval import nwis
    except ImportError as exc:
        write_empty(args.output, f"dataretrieval unavailable: {exc}")
        sys.exit(1)

    resolution = "hourly"
    try:
        series = np_util.retry(
            lambda: fetch_hourly(nwis, site, args.start, args.end),
            attempts=4, base_delay=10, what="NWIS get_iv",
        )
    except Exception as exc:  # noqa: BLE001 - degrade to daily, loudly
        print(
            f"Hourly (instantaneous) fetch failed for {site}: {exc}; "
            f"falling back to NWIS daily values — downstream metrics will "
            f"be daily-only",
            file=sys.stderr,
        )
        resolution = "daily"
        try:
            series = np_util.retry(
                lambda: fetch_daily(nwis, site, args.start, args.end),
                attempts=4, base_delay=10, what="NWIS get_dv",
            )
        except Exception as exc2:  # noqa: BLE001 - report and fail loudly
            write_empty(args.output, f"NWIS fetch failed: {exc2}")
            sys.exit(1)

    import pandas as pd

    out = pd.DataFrame({
        "datetime": series.index,
        "site_no": site,
        "discharge_cfs": series.values,
    })
    out.to_csv(args.output, index=False)
    print(f"Wrote {len(out)} {resolution} observations to {args.output}")


if __name__ == "__main__":
    main()
