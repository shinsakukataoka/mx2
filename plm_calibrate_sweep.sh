#!/usr/bin/env bash
# plm_calibrate_sweep.sh — calibration and portability validation sweep for the PLM.
#
# PURPOSE
# -------
# Two modes, both targeting sunnycove:
#
#   --mode calib    Generate n=4 calibration jobs (9 mixes × 8 freqs = 72 jobs).
#                   Each bench runs as 4 homogeneous copies → U_sum ≈ 4.
#                   Combined with the existing single-core oracle CSV (U_sum ≤ 1),
#                   this spans the full [0, 4] U_sum range needed to fit all three
#                   PLM coefficients (b_f, a_util, a_ipc) without extrapolation.
#
#   --mode validate Generate n=4 validation jobs to spot-check the fitted model
#                   against held-out oracle points at n=4.
#
# WHY n=4 FOR CALIBRATION (not n=1 or n=8)
# -----------------------------------------
# The existing single-core calibration CSV has U_sum ≤ 1 because each run has
# only one thread active (single-threaded SPEC on 8-core sim).  The deployment
# target (multi-programmed n=4) has U_sum up to 4.  Extrapolating a_util 4× is
# unacceptable for a calibrated model.  Running homogeneous n=4 jobs covers
# U_sum ≈ 4 directly.
#
# FULL WORKFLOW
# -------------
#  0. [Already done] Single-core oracle CSV exists at CALIB_N1_CSV (see below).
#     It covers U_sum ∈ [0, 1] across 19 frequencies.
#
#  1. Generate and run n=4 calibration jobs:
#       bash mx2/plm_calibrate_sweep.sh --mode calib
#       mx submit <CALIB_N4_RUN_DIR>
#       mx verify <CALIB_N4_RUN_DIR>
#
#  2. Extract oracle points from n=4 calibration runs:
#       SNIPER_HOME=... ROOT=<CALIB_N4_RUN_DIR>/runs \
#           bash mx2/tools/extract_oracle_points.sh
#
#  3. Fit model from combined n=1 (existing) + n=4 (new) data:
#       python3 mx2/tools/mcpat_plm_fit.py \
#           --csv <CALIB_N1_CSV> \
#           --extra-csv <CALIB_N4_RUN_DIR>/runs/oracle_points.csv \
#           --sniper-home $SNIPER_HOME \
#           --uarch sunnycove --calib-ncores 8 \
#           --out plm_sunnycove_cal.sh
#
#  (Optional) Validate on held-out n=4 runs:
#       bash mx2/plm_calibrate_sweep.sh --mode validate
#       ... (same extract + mcpat_plm_fit.py --validate-csv flow)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"
MX="$REPO_ROOT/mx2/bin/mx"
SITE_YAML="$REPO_ROOT/mx2/config/site.yaml"
OUT_BASE="$HOME/COSC_498/miniMXE/results_test"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
MODE=calib   # calib | validate
SIM_CORES=4  # override with --cores 8
L3_MB_LIST=""  # override with --l3-mb (comma-separated, e.g. "16,128")

# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --cores) SIM_CORES="$2"; shift 2 ;;
    --l3-mb) L3_MB_LIST="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--mode calib|validate] [--cores 4|8] [--l3-mb 16,128]"
      exit 0 ;;
    *) echo "[ERR] Unknown arg: $1"; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Sunnycove configuration
# ---------------------------------------------------------------------------
UARCH=sunnycove
TECH=sram14
# Existing single-core oracle CSV (U_sum ≤ 1, 19 frequencies, used as --csv in fit)
CALIB_N1_CSV="$HOME/COSC_498/miniMXE/results_test/calibration/sunnycove_spec10_l3_sram14_roi1000_warm200_SRAMONLY/spec/oracle_points_plus.csv"

case "$MODE" in
  calib)
    RUN_FREQS=( 2.0 2.1 2.2 2.3 2.4 2.5 2.6 2.7 2.8 2.9 3.0 3.1 3.2 3.3 3.4 3.5 3.6 3.7 3.8 3.9 4.0 )
    # Default: single LLC size for calibration
    [[ -z "$L3_MB_LIST" ]] && L3_MB_LIST="32"
    ;;
  validate)
    # Spot-check transferability to unseen LLC sizes
    RUN_FREQS=( 2.0 2.2 2.8 3.2 4.0 )
    # Default: two held-out LLC sizes (calibrated on 32M)
    [[ -z "$L3_MB_LIST" ]] && L3_MB_LIST="16,128"
    ;;
  *)
    echo "[ERR] Unknown mode: $MODE (supported: calib, validate)"
    exit 1 ;;
esac

# Parse comma-separated L3 sizes into array
IFS=',' read -r -a L3_SIZES <<< "$L3_MB_LIST"

ROI_M=1000
WARMUP_M=200
DIR_ENTRIES=4194304
BASE_PERIODIC_INS=2000000
FAIL_ON_SIFT_ASSERT=1

# ---------------------------------------------------------------------------
# Workloads
#   calib:    full canonical list from mx (stays in sync with run_traces/run_spec)
#   validate: 5 representative mixes (subset) for spot-checking
# ---------------------------------------------------------------------------
if [[ "$MODE" == "validate" ]]; then
  # Representative subset: compute-heavy, mem-heavy, mixed, homogeneous
  BENCHES=(
    "502.gcc_r+502.gcc_r+502.gcc_r+502.gcc_r"
    "505.mcf_r+500.perlbench_r+648.exchange2_s+649.fotonik3d_s"
    "505.mcf_r+505.mcf_r+502.gcc_r+502.gcc_r"
    "505.mcf_r+505.mcf_r+505.mcf_r+505.mcf_r"
    "523.xalancbmk_r+523.xalancbmk_r+502.gcc_r+502.gcc_r"
  )
else
  # Pull from mx's canonical lists so they stay in sync
  MX_BIN="$REPO_ROOT/mx2/bin/mx"
  if [[ "$SIM_CORES" -eq 1 ]]; then
    _func_name="default_spec_benches"
  else
    _func_name="default_trace_workloads"
  fi
  mapfile -t BENCHES < <(python3 -c "
import re
with open('${MX_BIN}') as f: src = f.read()
ns = {}; exec('from typing import List', ns)
m = re.search(r'(def ${_func_name}\(.*?\n(?:    .*\n)*)', src)
exec(m.group(1), ns)
for w in ns['${_func_name}'](${SIM_CORES}) if 'sim_n' in ns['${_func_name}'].__code__.co_varnames else ns['${_func_name}'](): print(w)
")
  if [[ ${#BENCHES[@]} -eq 0 ]]; then
    echo "[ERR] Failed to load workload list from mx for SIM_CORES=${SIM_CORES}" >&2
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# Read paths from site.yaml
# ---------------------------------------------------------------------------
read_yaml_key() {
  grep -E "^${1}:" "$2" | head -1 | sed 's/^[^:]*:[[:space:]]*//'
}
SNIPER_HOME="$(read_yaml_key SNIPER_HOME "$SITE_YAML")"
TRACE_ROOT="$(read_yaml_key  TRACE_ROOT  "$SITE_YAML")"
CONDA_LIB="$(read_yaml_key   CONDA_LIB  "$SITE_YAML")"
CONDA_PY="$(read_yaml_key    CONDA_PY   "$SITE_YAML")"
GCC_DIR="$(read_yaml_key     GCC_DIR    "$SITE_YAML")"

# ---------------------------------------------------------------------------
# Set up run directory
# ---------------------------------------------------------------------------
if [[ ${#L3_SIZES[@]} -eq 1 ]]; then
  RUN_ID="plm_${MODE}_${UARCH}_n${SIM_CORES}_${L3_SIZES[0]}M"
else
  RUN_ID="plm_${MODE}_${UARCH}_n${SIM_CORES}"
fi
RUN_DIR="$OUT_BASE/plm_calibrate/$RUN_ID"
RUNS_ROOT="$RUN_DIR/runs"

mkdir -p "$RUNS_ROOT" "$RUN_DIR/slurm"

cat > "$RUN_DIR/env.sh" <<ENVSH
#!/usr/bin/env bash
set -euo pipefail
export SNIPER_HOME='${SNIPER_HOME}'
export TRACE_ROOT='${TRACE_ROOT}'
export CONDA_LIB='${CONDA_LIB}'
export CONDA_PY='${CONDA_PY}'
export GCC_DIR='${GCC_DIR}'
export REPO_ROOT='${REPO_ROOT}'
ENVSH

# ---------------------------------------------------------------------------
# Generate jobs.txt
# ---------------------------------------------------------------------------
JOBS_FILE="$RUN_DIR/jobs.txt"
: > "$JOBS_FILE"
JOB_COUNT=0

for L3_MB in "${L3_SIZES[@]}"; do
  for FREQ in "${RUN_FREQS[@]}"; do
    FREQ_TAG="f${FREQ//./p}"
    for BENCH in "${BENCHES[@]}"; do
      BENCH_TAG="${BENCH//+/_}"
      if [[ ${#L3_SIZES[@]} -gt 1 ]]; then
        OUTDIR="${RUNS_ROOT}/${BENCH_TAG}/l3_${L3_MB}M/${FREQ_TAG}"
      else
        OUTDIR="${RUNS_ROOT}/${BENCH_TAG}/${FREQ_TAG}"
      fi
      _line="CAMPAIGN=plm_calib"
      _line+=" OUTDIR=${OUTDIR}"
      _line+=" JOB_OUTDIR=${OUTDIR}"
      _line+=" SNIPER_CONFIG=${UARCH}"
      _line+=" TECH=${TECH}"
      _line+=" WORKLOAD=${BENCH}"
      _line+=" L3_MB=${L3_MB}"
      _line+=" ROI_M=${ROI_M}"
      _line+=" WARMUP_M=${WARMUP_M}"
      _line+=" SIM_N=${SIM_CORES}"
      _line+=" BASE_FREQ_GHZ=${FREQ}"
      _line+=" BASE_PERIODIC_INS=${BASE_PERIODIC_INS}"
      _line+=" DIR_ENTRIES=${DIR_ENTRIES}"
      _line+=" FAIL_ON_SIFT_ASSERT=${FAIL_ON_SIFT_ASSERT}"
      echo "$_line" >> "$JOBS_FILE"
      (( JOB_COUNT++ )) || true
    done
  done
done

# ---------------------------------------------------------------------------
# Summary + next-step instructions
# ---------------------------------------------------------------------------
echo "=============================================="
echo " PLM sweep — mode=${MODE}  uarch=${UARCH}  N=${SIM_CORES}"
echo " LLC sizes: ${L3_SIZES[*]} MB   Tech: ${TECH}   Variant: baseline_sram_only"
echo " Frequencies: ${RUN_FREQS[*]} GHz"
echo " Benchmarks:  ${#BENCHES[@]}"
echo " Total jobs:  ${JOB_COUNT}"
echo " Run dir:     ${RUN_DIR}"
echo "=============================================="
echo
echo "[OK] planned ${JOB_COUNT} jobs -> ${RUN_DIR}"
echo

OUT_CAL_SH="$OUT_BASE/plm_calibrate/plm_sunnycove_cal.sh"

if [[ "$MODE" == "calib" ]]; then
  echo "After jobs complete:"
  echo
  echo "  # Extract oracle points from n=4 calibration runs:"
  echo "  SNIPER_HOME=${SNIPER_HOME} ROOT=${RUNS_ROOT} \\"
  echo "      bash ${REPO_ROOT}/mx2/tools/extract_oracle_points.sh"
  echo
  echo "  # Fit combined single-core (existing) + n=4 (new) model:"
  echo "  python3 ${REPO_ROOT}/mx2/tools/mcpat_plm_fit.py \\"
  echo "      --csv ${CALIB_N1_CSV} \\"
  echo "      --extra-csv ${RUNS_ROOT}/oracle_points.csv \\"
  echo "      --sniper-home ${SNIPER_HOME} \\"
  echo "      --uarch ${UARCH} --calib-ncores 4 \\"
  echo "      --out ${OUT_CAL_SH}"
else
  echo "After jobs complete:"
  echo
  echo "  # Extract oracle points from n=4 validation runs:"
  echo "  SNIPER_HOME=${SNIPER_HOME} ROOT=${RUNS_ROOT} \\"
  echo "      bash ${REPO_ROOT}/mx2/tools/extract_oracle_points.sh"
  echo
  echo "  # Validate (calib → n=4 held-out):"
  echo "  python3 ${REPO_ROOT}/mx2/tools/mcpat_plm_fit.py \\"
  echo "      --csv ${CALIB_N1_CSV} \\"
  echo "      --sniper-home ${SNIPER_HOME} \\"
  echo "      --uarch ${UARCH} --calib-ncores 4 \\"
  echo "      --validate-csv ${RUNS_ROOT}/oracle_points.csv \\"
  echo "      --validate-ncores 4 \\"
  echo "      --out ${OUT_CAL_SH}"
fi
