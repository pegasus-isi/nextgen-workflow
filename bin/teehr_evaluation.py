#!/usr/bin/env python3

"""TEEHR evaluation of the NextGen run (notebook 4).

Scores simulated streamflow for the spin-up and evaluation periods separately.
Uses TEEHR (which starts a local Spark session and reads public, anonymous S3
data) when it is importable and working; otherwise falls back to computing the
same KGE/NSE metrics with pandas against the USGS observations, which is the
fallback the spec calls for. The fallback is reported in the output so a reader
can tell which path produced the numbers.
"""

import argparse
import os
import sys
from pathlib import Path

import ngiab_pegasus as np_util


def metrics_from_series(sim, obs):
    """Return KGE, NSE, RMSE for two aligned array-likes."""
    import numpy as np

    sim = np.asarray(sim, dtype=float)
    obs = np.asarray(obs, dtype=float)
    mask = np.isfinite(sim) & np.isfinite(obs)
    out = {"n": int(mask.sum())}
    if mask.sum() < 2:
        return out
    sim, obs = sim[mask], obs[mask]
    out["rmse"] = float(np.sqrt(np.mean((sim - obs) ** 2)))
    denom = float(np.sum((obs - obs.mean()) ** 2))
    if denom > 0:
        out["nse"] = float(1 - np.sum((sim - obs) ** 2) / denom)
    if obs.std() > 0 and sim.std() > 0 and obs.mean() != 0:
        r = float(np.corrcoef(sim, obs)[0, 1])
        alpha = float(sim.std() / obs.std())
        beta = float(sim.mean() / obs.mean())
        out["kge"] = float(
            1 - ((r - 1) ** 2 + (alpha - 1) ** 2 + (beta - 1) ** 2) ** 0.5
        )
        out["r"] = r
        out["alpha"] = alpha
        out["beta"] = beta
    return out


def try_teehr(gage, gage_path, start, end, training_start):
    """Attempt the TEEHR/Spark evaluation. Returns rows or None.

    This job runs in CIROH's companion evaluation container
    (awiciroh/ngiab-teehr, selected in the transformation catalog), not the
    NGIAB engine container — TEEHR's dependency pins cannot coexist with the
    engine's. The container bundles /app/teehr_ngen.py (teehr 0.4 interface):
    a no-argument main() that reads the run directory from the module-level
    NGEN_DATA_DIR (default `./data`), fetches USGS observations and the NWM
    v3.0 retrospective from anonymous S3, joins them to the simulation, and
    writes `<data>/teehr/metrics.csv`.
    """
    try:
        import teehr  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"TEEHR unavailable ({exc}); using the pandas fallback",
              file=sys.stderr)
        return None

    metrics_csv = Path(gage_path, "teehr", "metrics.csv")
    try:
        if not os.path.exists("/app/teehr_ngen.py"):
            raise FileNotFoundError(
                "/app/teehr_ngen.py not found — teehr is importable but this "
                "does not look like the ngiab-teehr container"
            )
        # teehr_ngen resolves its data dir relative to the CWD at import time
        # (NGEN_DATA_DIR = Path('data')); point that at the extracted run dir.
        if os.path.lexists("data"):
            if not os.path.islink("data"):
                raise FileExistsError("a non-symlink ./data is in the way")
            os.remove("data")
        os.symlink(gage_path, "data")
        sys.path.insert(0, "/app")
        import teehr_ngen  # import creates <data>/teehr via the symlink

        try:
            teehr_ngen.main()
        except Exception as exc:  # noqa: BLE001
            # The trailing timeseries-plot step is marked experimental
            # upstream and can fail after metrics.csv is already written.
            if not metrics_csv.exists():
                raise
            print(f"teehr_ngen post-metrics step failed (ignored): {exc}",
                  file=sys.stderr)
        return _rows_from_teehr_csv(metrics_csv, gage, gage_path)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(
            f"TEEHR evaluation failed ({exc}); using the pandas fallback. "
            f"See SPEC.md section 7 risk 2.",
            file=sys.stderr,
        )
        return None


def _rows_from_teehr_csv(metrics_csv, gage, gage_path):
    """Map teehr_ngen's metrics.csv into this workflow's metrics schema.

    One input row per (location, configuration): `ngen` is our simulation,
    `nwm30_retrospective` is the NWM v3.0 benchmark — the comparison the
    pandas fallback cannot provide.
    """
    import pandas as pd

    df = pd.read_csv(metrics_csv)
    if df.empty:
        raise ValueError(f"{metrics_csv} is empty")
    feature_id = np_util.routing_feature_id(gage_path)
    metric_names = {
        "kling_gupta_efficiency": "kge",
        "nash_sutcliffe_efficiency": "nse",
        "relative_bias": "rel_bias",
        "root_mean_standard_deviation_ratio": "rmsdr",
    }
    rows = []
    for _, rec in df.iterrows():
        row = {
            "gage": gage,
            "feature_id": feature_id,
            "period": "full",
            "primary": str(rec.get("primary_location_id", "usgs_observed")),
            "secondary": str(rec.get("configuration_name", "ngen")),
            "method": "teehr",
        }
        for col, val in rec.items():
            key = metric_names.get(str(col).lower())
            if key and pd.notna(val):
                row[key] = float(val)
        rows.append(row)
    return rows


def _nwm_feature_id_candidates(gage, gage_path):
    """Yield (source, id) candidates for the gage's NWM v3.0 reach.

    NWM v3.0 feature ids are NHDPlus COMIDs; the USGS NLDI API (the paper's
    own mechanism, notebook 4) is the authoritative mapping. The hydrofabric
    subset's hydrolocations.hf_id is yielded second as a network-free backup,
    though in hydrofabric v2.2 it is a reference-fabric id that usually does
    NOT match the NWM index — the caller validates each candidate against the
    retrospective and keeps the first that works.
    """
    site = str(gage).split("-")[-1]

    try:
        import json
        from urllib.request import urlopen

        url = f"https://api.water.usgs.gov/nldi/linked-data/nwissite/USGS-{site}"
        with urlopen(url, timeout=60) as resp:
            data = json.load(resp)
        yield "NLDI", int(data["features"][0]["properties"]["comid"])
    except Exception as exc:  # noqa: BLE001
        print(f"NLDI COMID lookup failed ({exc})", file=sys.stderr)

    try:
        import sqlite3

        gpkg = next(Path(gage_path, "config").rglob("*.gpkg"), None)
        if gpkg is not None:
            with sqlite3.connect(str(gpkg)) as conn:
                found = conn.execute(
                    "SELECT hf_id FROM hydrolocations WHERE hl_uri LIKE ?",
                    (f"%-{site}",),
                ).fetchall()
            for fid in {int(float(r[0])) for r in found if r[0] is not None}:
                yield "hydrofabric hf_id", fid
    except Exception as exc:  # noqa: BLE001
        print(f"hydrofabric COMID lookup failed ({exc})", file=sys.stderr)


# NOAA's public NWM v3.0 retrospective (hourly, 1979-2023, anonymous access).
NWM_RETRO_ZARR = "s3://noaa-nwm-retrospective-3-0-pds/CONUS/zarr/chrtout.zarr"


def _nwm_retro_cfs(nwm_feature_id, start, end):
    """Hourly NWM v3.0 retrospective flow in cfs for one reach."""
    import xarray as xr

    ds = xr.open_zarr(
        NWM_RETRO_ZARR, storage_options={"anon": True}, consolidated=True
    )
    flow = (
        ds["streamflow"]
        .sel(feature_id=nwm_feature_id)
        .sel(time=slice(start, end))
        .load()
    )
    if flow.sizes.get("time", 0) == 0:
        raise ValueError(
            f"NWM v3.0 retrospective has no data for reach {nwm_feature_id} "
            f"in {start}..{end}"
        )
    # m^3/s -> cfs; the retrospective is natively hourly, which is the
    # paper's evaluation resolution — scoring aggregates to daily as needed.
    return flow.to_series() * 35.3147


def fallback_evaluation(gage, gage_path, obs_csv, start, end, training_start):
    """Score NextGen (and NWM v3.0) vs observed flow by period.

    Hourly is the primary resolution — the paper's Fig. 10 metrics are hourly
    (AUTHOR_REVIEW.md #2) — with a daily-mean aggregate emitted alongside for
    continuity with earlier runs. When the obs fetch degraded to daily values,
    an hourly join would only sample one midnight value per day, so only the
    daily rows are produced.
    """
    import pandas as pd

    feature_id = np_util.routing_feature_id(gage_path)
    troute_files = sorted(Path(gage_path, "outputs", "troute").glob("*.nc"))
    if not troute_files:
        raise FileNotFoundError(
            f"No t-route NetCDF under {gage_path}/outputs/troute"
        )

    sim_cfs = np_util.flow_series_cfs(troute_files[0], feature_id)
    if not isinstance(sim_cfs.index, pd.DatetimeIndex):
        # t-route emits one value per model hour from the simulation start.
        sim_cfs.index = pd.date_range(start=start, periods=len(sim_cfs),
                                      freq="h")

    obs = np_util.obs_series_cfs(obs_csv)
    resolutions = (
        ("hourly", "daily") if np_util.obs_resolution(obs) == "hourly"
        else ("daily",)
    )

    cut = pd.Timestamp(training_start)

    def score(series, secondary):
        out = []
        for resolution in resolutions:
            s = series if resolution == "hourly" else series.resample("D").mean()
            o = obs if resolution == "hourly" else obs.resample("D").mean()
            joined = pd.DataFrame({"sim": s}).join(
                o.rename("obs"), how="inner").dropna()
            periods = {
                "spinup": joined[joined.index < cut],
                "evaluation": joined[joined.index >= cut],
                "full": joined,
            }
            for name, frame in periods.items():
                if frame.empty:
                    continue
                row = {
                    "gage": gage,
                    "feature_id": feature_id,
                    "period": name,
                    "resolution": resolution,
                    "primary": "usgs_observed",
                    "secondary": secondary,
                    "method": "pandas_fallback",
                }
                row.update(metrics_from_series(frame["sim"], frame["obs"]))
                out.append(row)
        return out

    rows = []
    if obs is not None:
        rows.extend(score(sim_cfs, "nextgen_simulated"))

        # NWM v3.0 benchmark via direct fetch (SPEC section 7 risk 2). The
        # TEEHR warehouse crosswalk misses some gages — this workflow's demo
        # gage included — so resolve the reach and read NOAA's public
        # retrospective ourselves. Best-effort: the USGS comparison above
        # stands even if this fails.
        try:
            nwm_cfs = None
            for source, fid in _nwm_feature_id_candidates(gage, gage_path):
                try:
                    nwm_cfs = _nwm_retro_cfs(fid, start, end)
                    print(f"NWM v3.0 reach for {gage}: {fid} (from {source})")
                    break
                except (KeyError, ValueError) as exc:
                    print(f"NWM reach candidate {fid} (from {source}) "
                          f"not usable: {exc}", file=sys.stderr)
            if nwm_cfs is None:
                raise ValueError("no candidate reach id found in the "
                                 "NWM v3.0 retrospective")
            rows.extend(score(nwm_cfs, "nwm30_retrospective"))
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"NWM v3.0 comparison skipped ({exc}); "
                  f"the USGS comparison stands", file=sys.stderr)

    if not rows:
        rows.append({
            "gage": gage,
            "feature_id": feature_id,
            "period": "full",
            "method": "pandas_fallback",
            "note": "no overlapping observations available",
            "n": 0,
        })
    return rows


def _pillow_metrics_table(gage, rows, plot_path):
    """Text rendering of the metrics via Pillow.

    The ngiab-teehr container has no matplotlib but does ship Pillow; a
    readable table beats an empty file.
    """
    from PIL import Image, ImageDraw

    lines = [f"{gage}: streamflow skill (TEEHR container, no matplotlib)"]
    for r in rows:
        parts = [f"{r.get('secondary', '?')}/{r.get('period', '?')}"]
        if r.get("resolution"):
            parts[0] += f"/{r['resolution']}"
        for m in ("kge", "nse", "rel_bias", "rmsdr", "rmse"):
            if r.get(m) is not None:
                parts.append(f"{m}={r[m]:.3f}")
        lines.append("  ".join(parts))
    img = Image.new("RGB", (900, 40 + 20 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((20, 20 + 20 * i), line, fill="black")
    np_util.ensure_parent(plot_path)
    img.save(plot_path)
    print(f"Wrote {plot_path} (Pillow fallback)")


def plot_metrics(gage, rows, plot_path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        scored = [r for r in rows if r.get("kge") is not None]
        # The fallback emits hourly and daily rows for the same
        # (secondary, period); plot one resolution to keep the bars distinct,
        # preferring hourly (the paper's). TEEHR rows carry no resolution.
        if any(r.get("resolution") == "hourly" for r in scored):
            usable = [r for r in scored if r.get("resolution", "hourly") == "hourly"]
        else:
            usable = scored
        fig, ax = plt.subplots(figsize=(7, 4))
        if usable:
            labels = [
                f"{r['secondary']}/{r['period']}" if r.get("secondary")
                else r["period"]
                for r in usable
            ]
            for i, metric in enumerate(("kge", "nse")):
                vals = [r.get(metric) for r in usable]
                xs = [x + i * 0.35 for x in range(len(labels))]
                ax.bar(xs, [v if v is not None else 0 for v in vals],
                       width=0.35, label=metric.upper())
            ax.set_xticks([x + 0.175 for x in range(len(labels))])
            ax.set_xticklabels(labels)
            ax.axhline(0, color="black", lw=0.8)
            ax.set_ylabel("Score")
            ax.legend()
        else:
            ax.axis("off")
            ax.text(0.5, 0.5, f"{gage}: no scoreable periods",
                    ha="center", va="center")
        ax.set_title(f"{gage}: streamflow skill by period")
        fig.tight_layout()
        np_util.ensure_parent(plot_path)
        fig.savefig(plot_path, dpi=120)
        plt.close(fig)
        print(f"Wrote {plot_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"matplotlib plot unavailable ({exc}); trying Pillow",
              file=sys.stderr)
        try:
            _pillow_metrics_table(gage, rows, plot_path)
        except Exception as exc2:  # noqa: BLE001
            print(f"Metric plot skipped entirely: {exc2}", file=sys.stderr)
            np_util.ensure_parent(plot_path)
            open(plot_path, "wb").close()


def _ensure_declared_outputs(args):
    """Create empty declared outputs before a failing exit.

    Exiting without them makes HTCondor hold the job on stage-out instead of
    letting the DAG see the failure (see CLAUDE.md workflow-generation
    gotchas).
    """
    if args.output_metrics and not os.path.exists(args.output_metrics):
        np_util.ensure_parent(args.output_metrics)
        with open(args.output_metrics, "w") as fh:
            fh.write("gage,period,method,n\n")
    if args.output_plot and not os.path.exists(args.output_plot):
        np_util.ensure_parent(args.output_plot)
        with open(args.output_plot, "wb"):
            pass


def main():
    parser = argparse.ArgumentParser(description="TEEHR evaluation of a NextGen run")
    parser.add_argument("--gage", required=True, help="Gage ID")
    parser.add_argument("--run-tar", required=True,
                        help="Run-directory tarball from run_nextgen")
    parser.add_argument("--obs", default=None,
                        help="USGS observations CSV (used by the fallback path)")
    parser.add_argument("--start", required=True, help="Simulation start YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Simulation end YYYY-MM-DD")
    parser.add_argument("--training-start", required=True,
                        help="Boundary between spin-up and evaluation periods")
    parser.add_argument("--output-metrics", required=True, help="Output metrics CSV")
    parser.add_argument("--output-plot", required=True, help="Output PNG")
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
        print(f"teehr_evaluation failed: {exc}", file=sys.stderr)
        _ensure_declared_outputs(args)
        sys.exit(1)


def _main(args):
    np_util.configure_logging()
    home = np_util.job_home()
    gage_path = np_util.gage_dir(args.gage, home)
    np_util.extract_tar(args.run_tar, gage_path)
    np_util.data_root(home)

    rows = try_teehr(
        args.gage, gage_path, args.start, args.end, args.training_start
    )
    if rows is None:
        rows = fallback_evaluation(
            args.gage, gage_path, args.obs, args.start, args.end,
            args.training_start,
        )

    import pandas as pd

    df = pd.DataFrame(rows)
    np_util.ensure_parent(args.output_metrics)
    df.to_csv(args.output_metrics, index=False)
    print(df.to_string(index=False))
    print(f"Wrote {args.output_metrics}")

    plot_metrics(args.gage, rows, args.output_plot)


if __name__ == "__main__":
    main()
