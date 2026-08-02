#!/usr/bin/env python3

"""Fan-in: merge per-gage metrics into one table and a comparison figure.

Consumes the metrics CSVs from every gage's outputs_analysis and
teehr_evaluation jobs (and their calibrated counterparts when calibration ran).
File paths are passed explicitly as repeated --metrics arguments rather than
discovered by scanning, because Pegasus stages files individually.
"""

import argparse
import os
import sys

import ngiab_pegasus as np_util


def main():
    parser = argparse.ArgumentParser(description="Summarize metrics across gages")
    parser.add_argument("--metrics", action="append", required=True,
                        help="Metrics CSV (repeat for each input)")
    parser.add_argument("--output-csv", required=True, help="Combined CSV")
    parser.add_argument("--output-plot", required=True, help="Summary PNG")
    args = parser.parse_args()

    np_util.configure_logging()

    import pandas as pd

    frames = []
    for path in args.metrics:
        if not os.path.exists(path):
            print(f"Warning: missing metrics file {path}", file=sys.stderr)
            continue
        try:
            df = pd.read_csv(path)
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: could not read {path}: {exc}", file=sys.stderr)
            continue
        if df.empty:
            print(f"Warning: empty metrics file {path}", file=sys.stderr)
            continue
        df["source_file"] = os.path.basename(path)
        # Tag the calibrated variants so the two runs are distinguishable.
        df["variant"] = "calibrated" if "_cal" in os.path.basename(path) else "default"
        frames.append(df)

    if not frames:
        raise RuntimeError(
            f"None of the {len(args.metrics)} metrics files could be read; "
            f"nothing to summarize"
        )

    combined = pd.concat(frames, ignore_index=True, sort=False)
    np_util.ensure_parent(args.output_csv)
    combined.to_csv(args.output_csv, index=False)
    print(f"Wrote {args.output_csv} ({len(combined)} rows from {len(frames)} files)")
    print(combined.to_string(index=False))

    make_plot(combined, args.output_plot)


def make_plot(combined, plot_path):
    """Bar chart of KGE by gage. Never fails the job."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        np_util.ensure_parent(plot_path)

        if "kge" not in combined.columns or "gage" not in combined.columns:
            raise RuntimeError(
                f"no kge/gage columns to plot; have {list(combined.columns)}"
            )

        usable = combined.dropna(subset=["kge"])
        # Prefer the full-period rows when a period column exists.
        if "period" in usable.columns and (usable["period"] == "full").any():
            usable = usable[usable["period"] == "full"]
        if usable.empty:
            raise RuntimeError("no finite KGE values")

        fig, ax = plt.subplots(figsize=(max(6, 1.6 * usable["gage"].nunique()), 4))
        for i, (variant, group) in enumerate(usable.groupby("variant")):
            by_gage = group.groupby("gage")["kge"].max()
            xs = [x + i * 0.35 for x in range(len(by_gage))]
            ax.bar(xs, by_gage.values, width=0.35, label=variant)
            ax.set_xticks([x + 0.175 for x in range(len(by_gage))])
            ax.set_xticklabels(by_gage.index, rotation=30, ha="right")

        ax.axhline(0, color="black", lw=0.8)
        ax.set_ylabel("KGE")
        ax.set_title("NextGen streamflow skill by gage")
        ax.legend()
        fig.tight_layout()
        fig.savefig(plot_path, dpi=120)
        plt.close(fig)
        print(f"Wrote {plot_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"Summary plot skipped: {exc}", file=sys.stderr)
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(figsize=(8, 3))
            ax.axis("off")
            ax.text(0.5, 0.5, f"Summary plot unavailable\n{exc}",
                    ha="center", va="center", wrap=True)
            np_util.ensure_parent(plot_path)
            fig.savefig(plot_path, dpi=100)
            plt.close(fig)
        except Exception:  # noqa: BLE001
            np_util.ensure_parent(plot_path)
            open(plot_path, "wb").close()


if __name__ == "__main__":
    main()
