#!/usr/bin/env python3

"""Aggregate NextGen outputs to basin means and score against USGS (notebook 3).

Calls ngen_output_analysis() from the paper's own utility module (vendored under
bin/lib/), then computes RMSE and KGE against the observed hydrograph and writes
a water-balance figure.
"""

import argparse
import os
import sys

import ngiab_pegasus as np_util


def kge(sim, obs):
    """Kling-Gupta efficiency. Returns None when it cannot be computed."""
    import numpy as np

    sim = np.asarray(sim, dtype=float)
    obs = np.asarray(obs, dtype=float)
    mask = np.isfinite(sim) & np.isfinite(obs)
    if mask.sum() < 2:
        return None
    sim, obs = sim[mask], obs[mask]
    if obs.std() == 0 or sim.std() == 0 or obs.mean() == 0:
        return None
    r = float(np.corrcoef(sim, obs)[0, 1])
    alpha = float(sim.std() / obs.std())
    beta = float(sim.mean() / obs.mean())
    return float(1 - ((r - 1) ** 2 + (alpha - 1) ** 2 + (beta - 1) ** 2) ** 0.5)


def rmse(sim, obs):
    import numpy as np

    sim = np.asarray(sim, dtype=float)
    obs = np.asarray(obs, dtype=float)
    mask = np.isfinite(sim) & np.isfinite(obs)
    if mask.sum() < 1:
        return None
    return float(np.sqrt(np.mean((sim[mask] - obs[mask]) ** 2)))


def main():
    parser = argparse.ArgumentParser(
        description="Aggregate NextGen outputs and score against USGS"
    )
    parser.add_argument("--gage", required=True, help="Gage ID")
    parser.add_argument("--run-tar", required=True,
                        help="Run-directory tarball from run_nextgen")
    parser.add_argument("--obs", required=True, help="USGS observations CSV")
    parser.add_argument("--output-basin-means", required=True,
                        help="Output basin-mean CSV")
    parser.add_argument("--output-metrics", required=True,
                        help="Output metrics CSV")
    parser.add_argument("--output-plot", required=True,
                        help="Output water-balance PNG")
    args = parser.parse_args()

    np_util.configure_logging()
    home = np_util.job_home()
    gage_path = np_util.gage_dir(args.gage, home)
    np_util.extract_tar(args.run_tar, gage_path)

    # generate_paths() in the vendored module reads NGIAB_DATA_ROOT.
    np_util.data_root(home)
    sys.path.insert(0, os.getcwd())

    feature_id = np_util.routing_feature_id(gage_path)
    print(f"Analyzing {args.gage} (routing feature {feature_id})")

    import pandas as pd
    from ngen_outputs_utils import ngen_output_analysis, get_flow_data_from_netcdf

    agg = ngen_output_analysis(args.gage, feature_id)
    if agg is None:
        raise RuntimeError(
            "ngen_output_analysis() returned nothing - the run directory may be "
            "missing catchment outputs or the forcings file"
        )
    if not isinstance(agg, pd.DataFrame):
        agg = pd.DataFrame(agg)

    np_util.ensure_parent(args.output_basin_means)
    agg.to_csv(args.output_basin_means, index=False)
    print(f"Wrote {args.output_basin_means} "
          f"({len(agg)} rows, {len(agg.columns)} columns)")

    # --- Metrics vs observed streamflow --------------------------------------
    metrics = {"gage": args.gage, "feature_id": feature_id,
               "n_timesteps": len(agg)}

    from pathlib import Path

    troute_files = sorted(Path(gage_path, "outputs", "troute").glob("*.nc"))
    obs = pd.read_csv(args.obs)
    if troute_files and not obs.empty and "discharge_cfs" in obs.columns:
        sim_m3h = get_flow_data_from_netcdf(str(troute_files[0]), feature_id)
        sim = pd.Series([v / 3600.0 * 35.3147 for v in sim_m3h])  # -> cfs
        # Hourly simulation vs daily observations: aggregate to daily means and
        # compare over the overlapping length.
        sim_daily = sim.groupby(sim.index // 24).mean().reset_index(drop=True)
        obs_daily = obs["discharge_cfs"].reset_index(drop=True)
        n = min(len(sim_daily), len(obs_daily))
        if n > 1:
            metrics["n_days_compared"] = n
            metrics["rmse_cfs"] = rmse(sim_daily[:n], obs_daily[:n])
            metrics["kge"] = kge(sim_daily[:n], obs_daily[:n])
            metrics["mean_sim_cfs"] = float(sim_daily[:n].mean())
            metrics["mean_obs_cfs"] = float(obs_daily[:n].mean())
    else:
        print("Skipping streamflow metrics (no t-route output or no observations)",
              file=sys.stderr)

    np_util.ensure_parent(args.output_metrics)
    pd.DataFrame([metrics]).to_csv(args.output_metrics, index=False)
    print(f"Metrics: {metrics}")

    # --- Water balance figure -------------------------------------------------
    make_water_balance_plot(args.gage, agg, args.output_plot)


def make_water_balance_plot(gage, agg, plot_path):
    """Plot cumulative water-balance terms. Never fails the job."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # Column names follow the notebook's aggregation output.
        candidates = {
            "Precipitation": ["APCP_surface_m", "RAIN_RATE_m", "RAIN_RATE"],
            "Actual ET": ["ACTUAL_ET_m", "ACTUAL_ET", "E_OWP"],
            "Runoff": ["Q_OUT_m", "Q_OUT", "GIUH_RUNOFF"],
            "Soil storage": ["SOIL_STORAGE_m", "SOIL_STORAGE"],
        }
        fig, ax = plt.subplots(figsize=(11, 4.5))
        plotted = 0
        for label, options in candidates.items():
            col = next((c for c in options if c in agg.columns), None)
            if col is None:
                continue
            series = agg[col].astype(float).fillna(0.0)
            if label == "Soil storage":
                ax.plot(series.values, label=f"{label} ({col})", lw=0.9)
            else:
                ax.plot(series.cumsum().values, label=f"Cumulative {label} ({col})",
                        lw=0.9)
            plotted += 1

        if plotted == 0:
            raise RuntimeError(
                f"none of the expected water-balance columns present; "
                f"available: {list(agg.columns)[:15]}"
            )

        ax.set_xlabel("Time step")
        ax.set_ylabel("Depth (m)")
        ax.set_title(f"{gage}: NextGen water-balance terms")
        ax.legend(fontsize=8)
        fig.tight_layout()
        np_util.ensure_parent(plot_path)
        fig.savefig(plot_path, dpi=120)
        plt.close(fig)
        print(f"Wrote {plot_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"Water-balance plot skipped: {exc}", file=sys.stderr)
        np_util.ensure_parent(plot_path)
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(figsize=(8, 3))
            ax.axis("off")
            ax.text(0.5, 0.5, f"{gage}: water balance unavailable\n{exc}",
                    ha="center", va="center", wrap=True)
            fig.savefig(plot_path, dpi=100)
            plt.close(fig)
        except Exception:  # noqa: BLE001
            open(plot_path, "wb").close()


if __name__ == "__main__":
    main()
