#!/bin/bash
# run_manual.sh — run each pipeline step by hand, without Pegasus.
#
# This is validation gate 3 from SPEC.md section 12: a short single-gage slice
# that catches wrapper and container problems in minutes instead of waiting for a
# plan/submit cycle. Run it INSIDE the container, where pyngiab and
# ngiab_data_cli exist:
#
#   apptainer exec --bind "$PWD":/work --pwd /work \
#       Apptainer/NextGen_Container.sif bash run_manual.sh
#
# (build it first: apptainer build Apptainer/NextGen_Container.sif \
#      Apptainer/NextGen_Container.def)
#
# Override the defaults with environment variables:
#   GAGE=gage-10109001 START=2020-10-01 END=2020-12-31 bash run_manual.sh

set -euo pipefail

GAGE="${GAGE:-gage-10109001}"
START="${START:-2020-10-01}"
END="${END:-2020-12-31}"          # a 3-month slice keeps this quick
TRAINING_START="${TRAINING_START:-2020-11-01}"
WORK="${WORK:-./manual_test}"

# The wrappers import the vendored support modules from the working directory,
# exactly as Pegasus stages them.
export PYTHONPATH="$PWD/bin/lib:${PYTHONPATH:-}"

mkdir -p "$WORK"
cd "$WORK"
for lib in ../bin/lib/*.py; do cp -f "$lib" .; done

BIN=../bin

echo "=============================================================="
echo " NextGen manual pipeline test"
echo " gage=$GAGE  period=$START..$END  workdir=$WORK"
echo "=============================================================="

echo
echo "=== Step 0: fetch the CONUS hydrofabric cache (slow the first time) ==="
if [ -f hydrofabric_cache.tar ]; then
    echo "reusing existing hydrofabric_cache.tar"
else
    python3 "$BIN/fetch_hydrofabric.py" --gage "$GAGE" --output hydrofabric_cache.tar
fi
ls -lh hydrofabric_cache.tar

echo
echo "=== Step 1: subset the hydrofabric ==="
python3 "$BIN/subset_hydrofabric.py" \
    --gage "$GAGE" \
    --hydrofabric-cache hydrofabric_cache.tar \
    --output "${GAGE}_subset.tar"
tar -tf "${GAGE}_subset.tar" | head -5 || true  # SIGPIPE under pipefail is not an error

echo
echo "=== Step 2: generate AORC forcings ==="
python3 "$BIN/generate_forcings.py" \
    --gage "$GAGE" --subset-tar "${GAGE}_subset.tar" \
    --start "$START" --end "$END" \
    --output "${GAGE}_forcings.tar"
tar -tf "${GAGE}_forcings.tar" | head -5 || true  # SIGPIPE under pipefail is not an error

echo
echo "=== Step 3: generate + patch the realization ==="
python3 "$BIN/generate_realization.py" \
    --gage "$GAGE" --subset-tar "${GAGE}_subset.tar" \
    --start "$START" --end "$END" \
    --output "${GAGE}_realization.tar"

echo
echo "=== Step 4: assemble the run directory ==="
python3 "$BIN/assemble_rundir.py" \
    --gage "$GAGE" \
    --subset-tar "${GAGE}_subset.tar" \
    --forcings-tar "${GAGE}_forcings.tar" \
    --realization-tar "${GAGE}_realization.tar" \
    --output "rundir_${GAGE}.tar"
tar -tf "rundir_${GAGE}.tar" | head -8 || true  # SIGPIPE under pipefail is not an error

echo
echo "=== Step 5: fetch USGS observations ==="
python3 "$BIN/fetch_usgs_obs.py" \
    --gage "$GAGE" --start "$START" --end "$END" \
    --output "${GAGE}_usgs_obs.csv"
head -3 "${GAGE}_usgs_obs.csv"

echo
echo "=== Step 6: run NextGen + t-route (the slow step) ==="
python3 "$BIN/run_nextgen.py" \
    --gage "$GAGE" \
    --rundir-tar "rundir_${GAGE}.tar" \
    --obs "${GAGE}_usgs_obs.csv" \
    --output "rundir_${GAGE}_run.tar" \
    --plot "plots/${GAGE}_hydrograph.png"

echo
echo "=== Step 7: outputs analysis ==="
python3 "$BIN/outputs_analysis.py" \
    --gage "$GAGE" \
    --run-tar "rundir_${GAGE}_run.tar" \
    --obs "${GAGE}_usgs_obs.csv" \
    --output-basin-means "analysis/${GAGE}_basin_means.csv" \
    --output-metrics "analysis/${GAGE}_metrics.csv" \
    --output-plot "plots/${GAGE}_water_balance.png"
cat "analysis/${GAGE}_metrics.csv"

echo
echo "=== Step 8: TEEHR evaluation (falls back to pandas if TEEHR is absent) ==="
python3 "$BIN/teehr_evaluation.py" \
    --gage "$GAGE" \
    --run-tar "rundir_${GAGE}_run.tar" \
    --obs "${GAGE}_usgs_obs.csv" \
    --start "$START" --end "$END" \
    --training-start "$TRAINING_START" \
    --output-metrics "analysis/${GAGE}_teehr_metrics.csv" \
    --output-plot "plots/${GAGE}_teehr.png"
cat "analysis/${GAGE}_teehr_metrics.csv"

echo
echo "=== Step 9: summarize ==="
python3 "$BIN/summarize.py" \
    --metrics "analysis/${GAGE}_metrics.csv" \
    --metrics "analysis/${GAGE}_teehr_metrics.csv" \
    --output-csv "summary/summary_metrics.csv" \
    --output-plot "summary/summary_metrics.png"

echo
echo "=============================================================="
echo " Complete. Outputs:"
find analysis plots summary -type f 2>/dev/null | sort
echo "=============================================================="
echo
echo "Calibration is NOT exercised here — each DDS iteration is a full model"
echo "run. To try it:"
echo "  python3 $BIN/calibrate.py --gage $GAGE \\"
echo "      --rundir-tar rundir_${GAGE}.tar --obs ${GAGE}_usgs_obs.csv \\"
echo "      --start $START --end $END --training-start $TRAINING_START \\"
echo "      --repetitions 6 --output-params ${GAGE}_best_params.json \\"
echo "      --output-iterations calibration/${GAGE}_calibration_iterations.csv"
