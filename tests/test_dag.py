#!/usr/bin/env python3

"""Generator unit tests — validation gate 1 from SPEC.md section 12.

Runs the generator in-process and inspects the emitted workflow YAML. No Pegasus
submission, no container, no network: these are meant to run in seconds.

The most important check here is test_no_dangling_inputs: every job input must be
either a Replica Catalog entry or another job's declared output. A missing
producer is exactly the defect that made the assemble_rundir job necessary, and
it is invisible until plan time otherwise.

    pytest tests/ -v
"""

import os
import subprocess
import sys

import pytest
import yaml

WF_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATOR = os.path.join(WF_DIR, "workflow_generator.py")


def generate(tmp_path, *extra_args, raw_defaults=False):
    """Run the generator and return (workflow, replica_catalog) dicts.

    Calibration is on by default in the generator (6 repetitions); these
    shape tests pin --calibrate 0 unless the caller supplies a value, so the
    8N+2 base-DAG expectations stay explicit. Pass raw_defaults=True to test
    the generator's actual defaults.
    """
    out = tmp_path / "workflow.yml"
    cmd = [
        sys.executable, GENERATOR,
        "--gages", "gage-10109001",
        "-o", str(out),
    ]
    # Replace the default single gage when the caller supplies their own.
    if any(a == "--gages" for a in extra_args):
        cmd = [sys.executable, GENERATOR, "-o", str(out)]
    cmd.extend(extra_args)
    if not raw_defaults and not any(a == "--calibrate" for a in extra_args):
        cmd.extend(["--calibrate", "0"])

    result = subprocess.run(
        cmd, cwd=str(tmp_path), capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"generator failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    with open(out) as fh:
        wf = yaml.safe_load(fh)

    rc_path = tmp_path / "replicas.yml"
    rc = yaml.safe_load(open(rc_path)) if rc_path.exists() else {}
    return wf, rc


def job_ids(wf):
    return [j.get("id") for j in wf["jobs"]]


def declared_outputs(wf):
    out = set()
    for job in wf["jobs"]:
        for use in job.get("uses", []):
            if use.get("type") == "output":
                out.add(use["lfn"])
    return out


def declared_inputs(wf):
    pairs = []
    for job in wf["jobs"]:
        for use in job.get("uses", []):
            if use.get("type") == "input":
                pairs.append((job.get("id"), use["lfn"]))
    return pairs


def replica_lfns(rc):
    return {entry["lfn"] for entry in rc.get("replicas", [])}


# ----------------------------------------------------------------------
# DAG shape
# ----------------------------------------------------------------------
def test_single_gage_job_count(tmp_path):
    """8 jobs per gage + fetch_hydrofabric + summarize."""
    wf, _rc = generate(tmp_path)
    assert len(wf["jobs"]) == 8 * 1 + 2


def test_three_gage_job_count(tmp_path):
    wf, _rc = generate(
        tmp_path, "--gages", "gage-10109001", "gage-10154200", "gage-01427207"
    )
    assert len(wf["jobs"]) == 8 * 3 + 2


def test_calibration_adds_four_jobs_per_gage(tmp_path):
    """calibrate + apply_params + a second analysis/teehr pair."""
    wf, _rc = generate(tmp_path, "--calibrate", "6", "--training-start", "2020-10-01")
    assert len(wf["jobs"]) == 12 * 1 + 2


def test_parallel_dds_trials_fan_out(tmp_path):
    """--dds-trials N > 1 replaces the calibrate job with N seeded trial
    jobs plus a select_best_params reducer (8 + N + 1 + 3 jobs per gage)."""
    wf, _rc = generate(
        tmp_path, "--calibrate", "6", "--dds-trials", "3",
        "--training-start", "2020-10-01",
    )
    assert len(wf["jobs"]) == (8 + 3 + 1 + 3) * 1 + 2
    names = [j.get("name") for j in wf["jobs"]]
    assert names.count("calibrate") == 3
    assert names.count("select_best_params") == 1
    seeds = set()
    for job in wf["jobs"]:
        if job.get("name") == "calibrate":
            cal_args = [str(a) for a in job.get("arguments", [])]
            assert "--seed" in cal_args
            seeds.add(cal_args[cal_args.index("--seed") + 1])
    assert len(seeds) == 3, f"trials must have distinct seeds, got {seeds}"


def test_calibration_enabled_by_default(tmp_path):
    """With no --calibrate flag the generator enables the calibration branch."""
    wf, _rc = generate(tmp_path, raw_defaults=True)
    assert len(wf["jobs"]) == 12 * 1 + 2
    calibrate_jobs = [j for j in wf["jobs"] if j.get("name") == "calibrate"]
    assert len(calibrate_jobs) == 1
    cal_args = calibrate_jobs[0].get("arguments", [])
    assert "6" in [str(a) for a in cal_args], (
        f"default repetitions should be the paper demo's 6, got args: {cal_args}"
    )


def test_hydrofabric_tar_removes_fetch_job(tmp_path):
    """Supplying a cache tarball drops the shared fetch job and registers it."""
    cache = tmp_path / "hydrofabric_cache.tar"
    cache.write_bytes(b"not a real tarball")
    wf, rc = generate(tmp_path, "--hydrofabric-tar", str(cache))

    assert "fetch_hydrofabric" not in job_ids(wf)
    assert len(wf["jobs"]) == 8 * 1 + 1
    assert "hydrofabric_cache.tar" in replica_lfns(rc)


def test_unique_job_ids(tmp_path):
    wf, _rc = generate(
        tmp_path, "--gages", "gage-10109001", "gage-10154200",
        "--calibrate", "2", "--training-start", "2020-10-01",
    )
    ids = job_ids(wf)
    assert len(ids) == len(set(ids)), f"duplicate job ids: {ids}"


# ----------------------------------------------------------------------
# The load-bearing check
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "extra",
    [
        (),
        ("--calibrate", "2", "--training-start", "2020-10-01"),
        ("--calibrate", "6", "--dds-trials", "3",
         "--training-start", "2020-10-01"),
        ("--gages", "gage-10109001", "gage-10154200"),
    ],
)
def test_no_dangling_inputs(tmp_path, extra):
    """Every job input must be produced by a job or registered in the RC."""
    wf, rc = generate(tmp_path, *extra)
    produced = declared_outputs(wf)
    registered = replica_lfns(rc)

    dangling = [
        (job_id, lfn)
        for job_id, lfn in declared_inputs(wf)
        if lfn not in produced and lfn not in registered
    ]
    assert not dangling, (
        "inputs with no producer and no replica entry: "
        + ", ".join(f"{j} needs {f}" for j, f in dangling)
    )


def test_support_libs_registered_and_staged(tmp_path):
    """The vendored notebook modules must be in the RC and reach every job."""
    wf, rc = generate(tmp_path)
    registered = replica_lfns(rc)
    for lib in ("ngiab_pegasus.py", "ngen_outputs_utils.py", "forcings_utils.py",
                "cal_utils.py", "ngiab_utils.py"):
        assert lib in registered, f"{lib} missing from the replica catalog"

    # ngiab_pegasus is imported by every wrapper, so every job needs it.
    for job in wf["jobs"]:
        inputs = {u["lfn"] for u in job.get("uses", []) if u.get("type") == "input"}
        assert "ngiab_pegasus.py" in inputs, (
            f"job {job.get('id')} does not stage ngiab_pegasus.py"
        )


def test_run_dir_tarball_chain(tmp_path):
    """The run-dir tarball is produced before it is consumed, never mutated."""
    wf, _rc = generate(tmp_path)
    produced = declared_outputs(wf)
    for lfn in ("rundir_gage-10109001.tar", "rundir_gage-10109001_run.tar"):
        assert lfn in produced, f"{lfn} is never produced"

    # Exactly one job may declare each tarball as an output.
    for lfn in ("rundir_gage-10109001.tar", "rundir_gage-10109001_run.tar"):
        producers = [
            job.get("id") for job in wf["jobs"]
            for u in job.get("uses", [])
            if u.get("type") == "output" and u["lfn"] == lfn
        ]
        assert len(producers) == 1, f"{lfn} produced by {producers}"


def test_final_outputs_staged_out(tmp_path):
    """User-facing results are staged out; big intermediates are not."""
    wf, _rc = generate(tmp_path)
    stage_out = {}
    for job in wf["jobs"]:
        for use in job.get("uses", []):
            if use.get("type") == "output":
                stage_out[use["lfn"]] = use.get("stageOut", True)

    assert stage_out["summary/summary_metrics.csv"] is True
    assert stage_out["analysis/gage-10109001_metrics.csv"] is True
    # Run-directory tarballs are large intermediates.
    assert stage_out["rundir_gage-10109001.tar"] is False
    assert stage_out["rundir_gage-10109001_run.tar"] is False


# ----------------------------------------------------------------------
# Input validation
# ----------------------------------------------------------------------
def run_expect_failure(tmp_path, *args):
    result = subprocess.run(
        [sys.executable, GENERATOR, "-o", str(tmp_path / "wf.yml")] + list(args),
        cwd=str(tmp_path), capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        f"expected failure but got success:\n{result.stdout}"
    )
    return result.stdout + result.stderr


def test_requires_gages(tmp_path):
    assert "--gages" in run_expect_failure(tmp_path)


def test_rejects_reversed_dates(tmp_path):
    out = run_expect_failure(
        tmp_path, "--gages", "gage-1", "--start", "2021-01-01", "--end", "2020-01-01"
    )
    assert "must precede" in out


def test_rejects_training_start_outside_period(tmp_path):
    out = run_expect_failure(
        tmp_path, "--gages", "gage-1",
        "--start", "2017-10-01", "--end", "2021-09-30",
        "--training-start", "2025-01-01",
    )
    assert "training-start" in out


def test_rejects_missing_hydrofabric_tar(tmp_path):
    out = run_expect_failure(
        tmp_path, "--gages", "gage-1", "--hydrofabric-tar", "/nonexistent.tar"
    )
    assert "not found" in out


def test_deduplicates_gages(tmp_path):
    """A repeated gage would otherwise collide on job IDs."""
    wf, _rc = generate(tmp_path, "--gages", "gage-10109001", "gage-10109001")
    assert len(wf["jobs"]) == 8 * 1 + 2


# ----------------------------------------------------------------------
# Generator arguments vs wrapper argparse
# ----------------------------------------------------------------------
def test_argparse_contract(tmp_path):
    """Every flag a job passes must exist in its wrapper, and vice versa.

    Catches the classic drift where a generator argument is renamed but the
    wrapper is not (or the reverse), which otherwise only shows up as a job
    failure on a worker node.
    """
    import re
    import shlex

    # Calibration on, so every transformation appears in the DAG.
    wf, _rc = generate(tmp_path, "--calibrate", "2", "--training-start", "2020-10-01")

    problems = []
    for job in wf["jobs"]:
        script = os.path.join(WF_DIR, "bin", f"{job['name']}.py")
        assert os.path.exists(script), f"no wrapper for transformation {job['name']}"
        src = open(script).read()

        declared = set(re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"', src))
        required = set(
            re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"[^)]*required=True', src)
        )

        tokens = []
        for arg in job.get("arguments", []):
            tokens.extend(shlex.split(arg) if isinstance(arg, str) else [str(arg)])
        passed = {t for t in tokens if t.startswith("--")}

        for flag in sorted(passed - declared):
            problems.append(f"{job['name']}: job passes {flag}, wrapper rejects it")
        for flag in sorted(required - passed):
            problems.append(f"{job['name']}: wrapper requires {flag}, job omits it")

    assert not problems, "\n".join(problems)


def test_every_transformation_has_a_wrapper(tmp_path):
    """No transformation may reference a bin/ script that does not exist."""
    wf, _rc = generate(tmp_path, "--calibrate", "2", "--training-start", "2020-10-01")
    for name in {job["name"] for job in wf["jobs"]}:
        path = os.path.join(WF_DIR, "bin", f"{name}.py")
        assert os.path.exists(path), f"missing wrapper: bin/{name}.py"
        assert os.access(path, os.X_OK), f"wrapper not executable: bin/{name}.py"
