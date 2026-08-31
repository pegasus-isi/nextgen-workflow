"""Shared helpers for the NextGen Pegasus wrapper scripts.

Bridges the gap between NGIAB's conventions (a mutable directory tree under
$HOME, a separate Python virtualenv at /ngen/.venv) and Pegasus's conventions
(each job runs in its own flat scratch directory, state travels as declared
files).

Staged into each job's working directory via the Replica Catalog, so wrappers
import it as a plain top-level module.
"""

import json
import logging
import os
import subprocess
import sys
import tarfile
import time

logger = logging.getLogger(__name__)

# The NGIAB engine lives in its own virtualenv inside the container image, not
# in the interpreter running the wrapper. ngiab_data_cli lives in a second,
# isolated venv (its dependency pins conflict with the engine's) — the image
# sets NGIAB_CLI_PYTHON to point there; outside the image both fall back to the
# engine venv.
NGIAB_PYTHON = os.environ.get("NGIAB_PYTHON", "/ngen/.venv/bin/python")
NGIAB_CLI_PYTHON = os.environ.get("NGIAB_CLI_PYTHON", NGIAB_PYTHON)


def ensure_dir(path):
    """Create a directory, tolerating a dangling symlink at that path.

    os.makedirs(..., exist_ok=True) raises FileExistsError when the path is a
    symlink pointing nowhere, which is how PegasusLite sometimes presents
    declared output directories. os.path.lexists sees the link itself.
    """
    if not path:
        return
    if os.path.lexists(path) and not os.path.isdir(path):
        os.remove(path)
    os.makedirs(path, exist_ok=True)


def ensure_parent(file_path):
    """Create the parent directory of an output file path."""
    parent = os.path.dirname(file_path)
    if parent:
        ensure_dir(parent)


def job_home():
    """Return a private HOME inside the job's scratch directory.

    NGIAB caches the CONUS hydrofabric in $HOME/.ngiab and writes its output
    tree under $HOME. A Pegasus job must not touch the real home directory
    (it may not exist on a worker, and concurrent jobs would collide), so each
    job gets its own.
    """
    home = os.path.join(os.getcwd(), "job_home")
    ensure_dir(home)
    os.environ["HOME"] = home
    return home


def data_root(home=None):
    """Return the NGIAB preprocess output root for this job."""
    home = home or os.environ.get("HOME", os.getcwd())
    root = os.path.join(home, "ngiab_preprocess_output")
    ensure_dir(root)
    # Consumed by the vendored ngen_outputs_utils.generate_paths().
    os.environ["NGIAB_DATA_ROOT"] = root
    return root


def gage_dir(gage, home=None):
    """Return (and create) the per-gage NGIAB directory."""
    path = os.path.join(data_root(home), gage)
    ensure_dir(path)
    return path


def run_ngiab_cli(cli_args, cwd=None):
    """Invoke `python -m ngiab_data_cli` from its virtualenv.

    The notebooks do this with a shell magic that sources the venv activate
    script; calling the venv interpreter directly is equivalent and avoids a
    shell.
    """
    cmd = [NGIAB_CLI_PYTHON, "-m", "ngiab_data_cli"] + [str(a) for a in cli_args]
    logger.info("Running: %s", " ".join(cmd))
    # On a cold cache the CLI interactively asks (via rich.Prompt) whether to
    # download the hydrofabric — there is no flag or env var to preapprove, and
    # under Pegasus stdin is /dev/null, so Prompt.ask dies with EOFError. Feed
    # it "y" answers; extras are harmless when nothing prompts.
    result = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, input="y\n" * 8
    )
    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(
            f"ngiab_data_cli failed with exit code {result.returncode}: "
            f"{' '.join(cmd)}"
        )
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result


def extract_tar(tar_path, dest_dir):
    """Extract a tarball into dest_dir."""
    ensure_dir(dest_dir)
    logger.info("Extracting %s -> %s", tar_path, dest_dir)
    with tarfile.open(tar_path, "r") as tf:
        tf.extractall(dest_dir)


def create_tar(tar_path, base_dir, members):
    """Tar the given members (paths relative to base_dir) into tar_path.

    Members that do not exist are skipped with a warning rather than failing,
    so an optional subdirectory (e.g. an empty outputs/) does not break the job.
    """
    ensure_parent(tar_path)
    written = 0
    with tarfile.open(tar_path, "w") as tf:
        for member in members:
            full = os.path.join(base_dir, member)
            if not os.path.exists(full):
                logger.warning("Skipping missing tar member: %s", member)
                continue
            tf.add(full, arcname=member)
            written += 1
    if written == 0:
        raise RuntimeError(
            f"Refusing to write an empty tarball {tar_path}: none of the "
            f"expected members existed under {base_dir}: {members}"
        )
    logger.info("Wrote %s (%d members)", tar_path, written)


def retry(func, attempts=4, base_delay=5, what="operation"):
    """Call func() with exponential backoff. Re-raises the last exception.

    Used by the network-facing jobs (USGS, S3) so a transient failure does not
    take down the DAG.
    """
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - deliberately broad
            last = exc
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "%s failed (attempt %d/%d): %s - retrying in %ds",
                what, attempt, attempts, exc, delay,
            )
            time.sleep(delay)
    raise last


def find_gpkg(gage_path):
    """Return the subset GeoPackage path inside an NGIAB run directory."""
    config_dir = os.path.join(gage_path, "config")
    for root, _dirs, files in os.walk(config_dir):
        for name in files:
            if name.endswith(".gpkg"):
                return os.path.join(root, name)
    raise FileNotFoundError(f"No .gpkg found under {config_dir}")


def realization_path(gage_path):
    return os.path.join(gage_path, "config", "realization.json")


def load_json(path):
    with open(path) as fh:
        return json.load(fh)


def dump_json(obj, path, indent=4):
    ensure_parent(path)
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=indent)


def routing_feature_id(gage_path):
    """Resolve the t-route feature id for the gage from the hydrofabric.

    The notebooks hardcoded this value after running an interactive query. It is
    derived here so the DAG has no hidden state.
    """
    sys.path.insert(0, os.getcwd())
    import ngiab_utils  # vendored support module

    pairs = ngiab_utils.get_gages_from_hydrofabric(gage_path)
    if not pairs:
        raise RuntimeError(f"No gages found in the hydrofabric at {gage_path}")
    # pairs are (flowpath_id like 'wb-2861391', gage_no)
    wb_id = pairs[0][0]
    return int(str(wb_id).split("-")[-1])


def flow_series_cfs(nc_path, feature_id):
    """Simulated flow from a t-route NetCDF as an hourly cfs pandas Series.

    Values come from the paper's get_flow_data_from_netcdf (m^3/h). The index
    is the file's own time coordinate when it is present and matches the
    series length, so callers can align on timestamps instead of positions
    (the paper evaluates hourly — AUTHOR_REVIEW.md #2). When the time
    coordinate cannot be read, the index stays positional and callers must
    fall back to positional alignment.
    """
    import pandas as pd

    sys.path.insert(0, os.getcwd())
    from ngen_outputs_utils import get_flow_data_from_netcdf

    values = get_flow_data_from_netcdf(str(nc_path), feature_id)
    sim = pd.Series([v / 3600.0 * 35.3147 for v in values])  # m^3/h -> cfs
    try:
        import xarray as xr

        with xr.open_dataset(nc_path) as ds:
            if "time" in ds and ds["time"].size == len(sim):
                idx = pd.DatetimeIndex(pd.to_datetime(ds["time"].values))
                if idx.tz is not None:
                    idx = idx.tz_localize(None)
                sim.index = idx
    except Exception as exc:  # noqa: BLE001 - positional fallback stands
        logger.warning("Could not read the time coordinate from %s (%s); "
                       "flow series keeps a positional index", nc_path, exc)
    return sim


def obs_series_cfs(obs_csv):
    """Observed discharge from a staged NWIS CSV as a UTC-naive cfs Series.

    Returns None when the file is missing, empty, or lacks the expected
    columns. Handles both tz-aware instantaneous timestamps and the naive
    dates of the daily-values fallback.
    """
    import pandas as pd

    if not obs_csv or not os.path.exists(obs_csv):
        return None
    frame = pd.read_csv(obs_csv)
    if frame.empty or "discharge_cfs" not in frame.columns \
            or "datetime" not in frame.columns:
        return None
    when = pd.to_datetime(frame["datetime"], utc=True).dt.tz_localize(None)
    return pd.Series(frame["discharge_cfs"].values, index=when).sort_index()


def obs_resolution(obs):
    """'hourly' or 'daily', from the median spacing of an observation series."""
    import pandas as pd

    if obs is None or len(obs) < 3 or not isinstance(obs.index, pd.DatetimeIndex):
        return "daily"
    step = obs.index.to_series().diff().median()
    return "hourly" if step <= pd.Timedelta(hours=6) else "daily"


def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
