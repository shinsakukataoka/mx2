#!/usr/bin/env bash
# vf_sensitivity_study.sh
#
# Sensitivity study: piecewise-linear power model (PLM) for LeakDVFS.
# Uarch: sunnycove, LLC: 32MB, Cores: n=4, Campaign: vf_sensitivity
#
# Model: P_est = llc_leak_w + b_f + a_util*avg_util + a_ipc*ipc_interval
# One linear entry per DVFS operating frequency; exact lookup, nearest fallback.
#
# Usage:
#   bash vf_sensitivity_study.sh              # plan jobs
#   mx submit <RUN_DIR>                       # submit to SLURM
#   mx verify <RUN_DIR>                       # check status after runs
#
# Note: mx validate does not support the vf_sensitivity campaign — skip it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"
MX="$REPO_ROOT/mx2/bin/mx"
SITE_YAML="$REPO_ROOT/mx2/config/site.yaml"
OUT_BASE="$HOME/COSC_498/miniMXE/results_test"

# ---------------------------------------------------------------------------
# PLM coefficient file override (optional)
# Leave empty to use built-in placeholder coefficients in run_vf_sensitivity.sh.
# Set to an absolute path of a shell snippet that re-defines PLM_N, PLM_F,
# PLM_B, PLM_AUTIL, PLM_AIPC arrays (see run_vf_sensitivity.sh header).
# ---------------------------------------------------------------------------
PLM_CFG_SH="${PLM_CFG_SH:-}"

# ---------------------------------------------------------------------------
# Fixed study dimensions
# ---------------------------------------------------------------------------
UARCH=sunnycove
CORES=4
L3_MB=32
BASE_FREQ_GHZ=2.2
LC_FMIN_GHZ=1.6
ROI_M=1000
WARMUP_M=200
DIR_ENTRIES=4194304
BASE_PERIODIC_INS=2000000
FAIL_ON_SIFT_ASSERT=1

# LC variant label built from params.yaml (per-workload PLM cap)
PARAMS_YAML="$REPO_ROOT/mx2/config/params.yaml"

# Helper: get PLM cap for a workload; falls back to linear cap_w
get_plm_cap() {
  local _wl="$1"
  python3 -c "
import yaml, sys
p = yaml.safe_load(open('$PARAMS_YAML'))
sc = p['uarch']['sunnycove']
try:
    cap = sc['plm_cap_w']['n${CORES}']['${_wl}'][${L3_MB}]
    if cap and float(cap) > 0:
        print(f'{cap:.2f}')
        sys.exit(0)
except (KeyError, TypeError):
    pass
# fallback to linear cap_w
try:
    cap = sc['cap_w']['multicore']['n${CORES}'][${L3_MB}]
    print(f'{cap:.2f}')
except (KeyError, TypeError):
    cap = sc['cap_w']['single'][${L3_MB}]
    print(f'{cap:.2f}')
" 2>/dev/null
}

_p_static=$(python3 -c "
import yaml
p = yaml.safe_load(open('$PARAMS_YAML'))
print(f\"{p['uarch']['sunnycove']['power']['p_static_w']:.2f}\")
" 2>/dev/null)

_k_dyn=$(python3 -c "
import yaml
p = yaml.safe_load(open('$PARAMS_YAML'))
print(f\"{p['uarch']['sunnycove']['power']['k_dyn_w_per_ghz_util']:.2f}\")
" 2>/dev/null)

_stat_lbl="${_p_static//./$'p'}"
_dyn_lbl="${_k_dyn//./$'p'}"

# Build LC_BASE label for a given cap value
make_lc_base() {
  local _cap="$1"
  local _cap_lbl="${_cap//./$'p'}"
  echo "lc_c${_cap_lbl}_s${_stat_lbl}_d${_dyn_lbl}_tf1_h0p35_f4_st0p15_pi${BASE_PERIODIC_INS}"
}

# ---------------------------------------------------------------------------
# 5 calibrated n=4 workload mixes (matching plm_cap_w in params.yaml)
# ---------------------------------------------------------------------------
WORKLOADS=(
  "502.gcc_r+502.gcc_r+502.gcc_r+502.gcc_r"
  "505.mcf_r+500.perlbench_r+648.exchange2_s+649.fotonik3d_s"
  "505.mcf_r+505.mcf_r+502.gcc_r+502.gcc_r"
  "505.mcf_r+505.mcf_r+505.mcf_r+505.mcf_r"
  "523.xalancbmk_r+523.xalancbmk_r+502.gcc_r+502.gcc_r"
)

# ---------------------------------------------------------------------------
# 6 (variant:tech) pairs — mirrors main study conventions
# ---------------------------------------------------------------------------
# VARPAIRS is now built per-workload (LC_BASE varies with cap)
# Static baselines (no LC)
STATIC_VARPAIRS=(
  "baseline_sram_only:sram7"
  "baseline_sram_only:sram14"
  "baseline_mram_only:mram14"
)
# LC variants are added per-workload in the loop below

# ---------------------------------------------------------------------------
# Read paths from site.yaml
# ---------------------------------------------------------------------------
read_yaml_key() {
  grep -E "^${1}:" "$2" | head -1 | sed 's/^[^:]*:[[:space:]]*//'
}

SNIPER_HOME="$(read_yaml_key SNIPER_HOME "$SITE_YAML")"
TRACE_ROOT="$(read_yaml_key TRACE_ROOT "$SITE_YAML")"
CONDA_LIB="$(read_yaml_key CONDA_LIB "$SITE_YAML")"
CONDA_PY="$(read_yaml_key CONDA_PY "$SITE_YAML")"
GCC_DIR="$(read_yaml_key GCC_DIR "$SITE_YAML")"

# ---------------------------------------------------------------------------
# Set up run directory
# ---------------------------------------------------------------------------
RUN_ID="vf_sensitivity"
RUN_DIR="$OUT_BASE/vf_sensitivity/$RUN_ID"
RUNS_ROOT="$RUN_DIR/runs"

mkdir -p "$RUNS_ROOT" "$RUN_DIR/slurm"

# env.sh (sourced by array_runner.sbatch before dispatch)
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

emit_job() {
  local VARIANT="$1" TECH="$2" WORKLOAD="$3"
  local OUTDIR="${RUNS_ROOT}/${WORKLOAD}/n${CORES}/l3_${L3_MB}MB/${VARIANT}_${TECH}"
  local _line="CAMPAIGN=vf_sensitivity"
  _line+=" OUTDIR=${OUTDIR}"
  _line+=" JOB_OUTDIR=${OUTDIR}"
  _line+=" SNIPER_CONFIG=${UARCH}"
  _line+=" TECH=${TECH}"
  _line+=" WORKLOAD=${WORKLOAD}"
  _line+=" L3_MB=${L3_MB}"
  _line+=" VARIANT=${VARIANT}"
  _line+=" ROI_M=${ROI_M}"
  _line+=" WARMUP_M=${WARMUP_M}"
  _line+=" SIM_N=${CORES}"
  _line+=" BASE_FREQ_GHZ=${BASE_FREQ_GHZ}"
  _line+=" BASE_PERIODIC_INS=${BASE_PERIODIC_INS}"
  _line+=" LC_FMIN_GHZ=${LC_FMIN_GHZ}"
  _line+=" DIR_ENTRIES=${DIR_ENTRIES}"
  _line+=" FAIL_ON_SIFT_ASSERT=${FAIL_ON_SIFT_ASSERT}"
  [[ -n "${PLM_CFG_SH}" ]] && _line+=" PLM_CFG_SH=${PLM_CFG_SH}"
  echo "$_line" >> "$JOBS_FILE"
  (( JOB_COUNT++ )) || true
}

for WORKLOAD in "${WORKLOADS[@]}"; do
  # Static baselines (no per-workload cap)
  for PAIR in "${STATIC_VARPAIRS[@]}"; do
    IFS=':' read -r VARIANT TECH <<< "$PAIR"
    emit_job "$VARIANT" "$TECH" "$WORKLOAD"
  done

  # LC variants — per-workload PLM cap
  _cap=$(get_plm_cap "$WORKLOAD")
  LC_BASE=$(make_lc_base "$_cap")
  emit_job "${LC_BASE}" "mram14" "$WORKLOAD"
  emit_job "naive_${LC_BASE}" "mram14" "$WORKLOAD"
  emit_job "sram_${LC_BASE}" "mram14" "$WORKLOAD"
done

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "=============================================="
echo " PLM Sensitivity Study"
echo " Uarch:       ${UARCH}   Cores: n=${CORES}   LLC: ${L3_MB}MB"
echo " Power model: piecewise-linear (PLM)"
echo " PLM cfg:     ${PLM_CFG_SH:-<builtin defaults in run_vf_sensitivity.sh>}"
echo " LC label:    per-workload (from plm_cap_w in params.yaml)"
echo " Workloads:   ${#WORKLOADS[@]}"
echo " Variants:    ${#STATIC_VARPAIRS[@]} static + 3 LC per workload"
echo " Total jobs:  ${JOB_COUNT}"
echo " Run dir:     ${RUN_DIR}"
echo "=============================================="
echo
echo "[OK] planned ${JOB_COUNT} jobs -> ${RUN_DIR}"
echo
echo "Next steps:"
echo "  1. Compile modified Sniper (if not done)"
echo "  2. Submit:  $MX submit ${RUN_DIR}"
echo "  3. Verify:  $MX verify ${RUN_DIR}"
