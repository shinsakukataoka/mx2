#!/usr/bin/env bash
# sunnycove_hca_study.sh
#
# Master script for the SunnyCove HCA study.
# Plans 4 sub-studies under results_test/hca/sunnycove_hca/:
#
#   1) Cross-node comparison      — 5 tech configs × 30 patterns =  150 jobs
#   2) Static policy comparison    — 6 HCA variants × 30 patterns =  180 jobs
#   3) Migration sweep             — 48 mig configs × 30 patterns = 1440 jobs
#   4) Latency sweep               — 4 lat points  × 30 patterns =  120 jobs
#                                                          Total  = 1890 jobs
#
# Common parameters:
#   uarch       = sunnycove
#   base_freq   = 2.2 GHz
#   cores       = 1 (single-core)
#   benchmarks  = 10 SPEC (500,502,505,520,523,531,541,557,648,649)
#   L3 sizes    = 16, 32, 128 MB
#
# Usage:
#   bash sunnycove_hca_study.sh              # plan all 4 sub-studies
#   mx submit <run_dir>                      # submit any sub-study to SLURM
#   mx verify <run_dir>                      # check status after runs
#
set -euo pipefail

# -----------------------
# Fixed paths
# -----------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"
MX="$REPO_ROOT/mx2/bin/mx"
DEV_DIR="$REPO_ROOT/mx2/config/devices"
OUT_BASE="$HOME/COSC_498/miniMXE/results_test"

[[ -x "$MX" ]] || { echo "[ERR] missing mx: $MX"; exit 1; }

# -----------------------
# Common knobs
# -----------------------
UARCH="sunnycove"
BASE_FREQ_GHZ="2.2"
CORES=1
ROI_M=1000
WARMUP_M=200

# 10 SPEC benchmarks
BENCHES="500.perlbench_r,502.gcc_r,505.mcf_r,520.omnetpp_r,523.xalancbmk_r,531.deepsjeng_r,541.leela_r,557.xz_r,648.exchange2_s,649.fotonik3d_s"

# 3 L3 sizes
L3_LIST="16,32,128"

STUDY_ROOT="sunnycove_hca"

TOTAL_JOBS=0

# Helper: plan + validate a sub-study
plan_and_validate() {
  local run_id="$1"; shift
  local sram_tech="$1"; shift
  local mram_tech="$1"; shift
  local tech_tag="$1"; shift
  local variants="$1"; shift

  local run_dir="$OUT_BASE/hca/$run_id"

  "$MX" plan-hca \
    --out "$OUT_BASE" \
    --run-id "$run_id" \
    --uarch "$UARCH" \
    --sram-tech "$sram_tech" \
    --mram-tech "$mram_tech" \
    --tech-tag "$tech_tag" \
    --benches "$BENCHES" \
    --l3 "$L3_LIST" \
    --cores "$CORES" \
    --roi-m "$ROI_M" \
    --warmup-m "$WARMUP_M" \
    --base-freq-ghz "$BASE_FREQ_GHZ" \
    --variants "$variants"

  "$MX" validate "$run_dir"

  # Count jobs
  local n
  n=$(wc -l < "$run_dir/jobs.txt")
  TOTAL_JOBS=$(( TOTAL_JOBS + n ))
  echo "[OK] $run_id -> $n jobs"
}

# ===============================================================
# STUDY 1: Cross-Node Comparison (5 tech configs × 30 = 150 jobs)
# ===============================================================
# Each tech node produces a homogeneous cache (all-SRAM or all-MRAM).
# SRAM16 ≡ sram14 (14nm), MRAM16 ≡ mram14 (14nm).
echo ""
echo "=============================================="
echo " Study 1: Cross-Node Comparison"
echo "=============================================="

# For SRAM baselines: variant=baseline_sram_only, only sram_tech matters
# mram_tech set to mram14 (not used when sram_ways=16)
plan_and_validate "${STUDY_ROOT}/1_cross_node/sram7"  sram7  mram14 sram7  "baseline_sram_only"
plan_and_validate "${STUDY_ROOT}/1_cross_node/sram14" sram14 mram14 sram14 "baseline_sram_only"
plan_and_validate "${STUDY_ROOT}/1_cross_node/sram32" sram32 mram14 sram32 "baseline_sram_only"

# For MRAM baselines: variant=baseline_mram_only, only mram_tech matters
# sram_tech set to sram14 (not used when sram_ways=0)
plan_and_validate "${STUDY_ROOT}/1_cross_node/mram14" sram14 mram14 mram14 "baseline_mram_only"
plan_and_validate "${STUDY_ROOT}/1_cross_node/mram32" sram14 mram32 mram32 "baseline_mram_only"

# ===============================================================
# STUDY 2: Static Policy Comparison (6 configs × 30 = 180 jobs)
# ===============================================================
# grid_* = parity-based (set-parity, way-restricted)
# noparity_* = canonical mixed-way (no set-parity, full assoc)
# All use sram14 + mram14 device pair.
echo ""
echo "=============================================="
echo " Study 2: Static Policy Comparison"
echo "=============================================="

STATIC_VARIANTS="\
grid_s4_fillmram,\
grid_s8_fillmram,\
grid_s12_fillmram,\
noparity_s4_fillmram,\
noparity_s8_fillmram,\
noparity_s12_fillmram"

plan_and_validate "${STUDY_ROOT}/2_static_policy" sram14 mram14 sram14_mram14 "$STATIC_VARIANTS"

# ===============================================================
# STUDY 3: Migration Sweep (48 configs × 30 = 1440 jobs)
# ===============================================================
# Cross product: SRAM ways {4,8,12} × Promote {2,4,8,16} × Cooldown {8,16,32,64}
# All noparity (canonical mixed-way, no set-parity).
# Uses sram14 + mram14.
echo ""
echo "=============================================="
echo " Study 3: Migration Sweep"
echo "=============================================="

SRAM_WAYS=(4 8 12)
PROMOTE=(2 4 8 16)
COOLDOWN=(8 16 32 64)

MIG_VARIANTS=""
for sw in "${SRAM_WAYS[@]}"; do
  for p in "${PROMOTE[@]}"; do
    for c in "${COOLDOWN[@]}"; do
      [[ -n "$MIG_VARIANTS" ]] && MIG_VARIANTS+=","
      MIG_VARIANTS+="noparity_s${sw}_fillmram_p${p}_c${c}"
    done
  done
done

# Verify config count
IFS=',' read -ra MIG_ARR <<< "$MIG_VARIANTS"
echo "[INFO] migration configs: ${#MIG_ARR[@]} (expected 48)"

plan_and_validate "${STUDY_ROOT}/3_migration_sweep" sram14 mram14 sram14_mram14 "$MIG_VARIANTS"

# ===============================================================
# STUDY 4: Latency Sweep (4 latency points × 30 = 120 jobs)
# ===============================================================
# Scale MRAM read+write latency by 2x, 3x, 4x, 5x (both together).
# Uses existing mram14_rNx_wNx.yaml device files.
# HCA variant: noparity_s8_fillmram (static, canonical, 8 SRAM ways).
echo ""
echo "=============================================="
echo " Study 4: Latency Sweep"
echo "=============================================="

LAT_VARIANT="noparity_s8_fillmram"

for MULT in 2 3 4 5; do
  MRAM_TECH="mram14_r${MULT}x_w${MULT}x"

  # Verify device file exists
  [[ -f "$DEV_DIR/${MRAM_TECH}.yaml" ]] || { echo "[ERR] missing device file: $DEV_DIR/${MRAM_TECH}.yaml"; exit 1; }

  plan_and_validate \
    "${STUDY_ROOT}/4_latency_sweep/lat_${MULT}x" \
    sram14 \
    "$MRAM_TECH" \
    "sram14_${MRAM_TECH}" \
    "$LAT_VARIANT"
done

# ===============================================================
# STUDY 5: Focused Latency Sweep (3 variants × 4 lat × 10 = 120 jobs)
# ===============================================================
# Tests latency sensitivity for 3 key configs at 16MB only:
#   - baseline_mram_only (all MRAM)
#   - noparity_s4_fillmram (static mixed, 4 SRAM / 12 MRAM ways)
#   - noparity_s4_fillmram_p4_c32 (migration, 4 SRAM ways, promote=4, cooldown=32)
# Latency scales: 2x, 3x, 4x, 5x
echo ""
echo "=============================================="
echo " Study 5: Focused Latency Sweep (16MB only)"
echo "=============================================="

FOCUSED_VARIANTS="baseline_mram_only,noparity_s4_fillmram,noparity_s4_fillmram_p4_c32"

for MULT in 2 3 4 5; do
  MRAM_TECH="mram14_r${MULT}x_w${MULT}x"
  [[ -f "$DEV_DIR/${MRAM_TECH}.yaml" ]] || { echo "[ERR] missing device file: $DEV_DIR/${MRAM_TECH}.yaml"; exit 1; }

  # Override L3_LIST to 16 only for this study
  "$MX" plan-hca \
    --out "$OUT_BASE" \
    --run-id "${STUDY_ROOT}/5_focused_latency_sweep/lat_${MULT}x" \
    --uarch "$UARCH" \
    --sram-tech sram14 \
    --mram-tech "$MRAM_TECH" \
    --tech-tag "sram14_${MRAM_TECH}" \
    --benches "$BENCHES" \
    --l3 "16" \
    --cores "$CORES" \
    --roi-m "$ROI_M" \
    --warmup-m "$WARMUP_M" \
    --base-freq-ghz "$BASE_FREQ_GHZ" \
    --variants "$FOCUSED_VARIANTS"

  local_dir="$OUT_BASE/hca/${STUDY_ROOT}/5_focused_latency_sweep/lat_${MULT}x"
  "$MX" validate "$local_dir"
  n=$(wc -l < "$local_dir/jobs.txt")
  TOTAL_JOBS=$(( TOTAL_JOBS + n ))
  echo "[OK] 5_focused_latency_sweep/lat_${MULT}x -> $n jobs"
done

# ===============================================================
# Summary
# ===============================================================
echo ""
echo "=============================================="
echo " SunnyCove HCA Study — Complete"
echo "=============================================="
echo " Uarch:       ${UARCH}"
echo " Base freq:   ${BASE_FREQ_GHZ} GHz"
echo " Cores:       ${CORES}"
echo " Benchmarks:  10 SPEC"
echo " L3 sizes:    ${L3_LIST} MB (study 5: 16MB only)"
echo " Total jobs:  ${TOTAL_JOBS}"
echo " Results dir: ${OUT_BASE}/hca/${STUDY_ROOT}/"
echo "=============================================="
echo ""
echo "Next steps:"
echo "  Submit each sub-study separately:"
echo "    $MX submit ${OUT_BASE}/hca/${STUDY_ROOT}/1_cross_node/sram7"
echo "    $MX submit ${OUT_BASE}/hca/${STUDY_ROOT}/1_cross_node/sram14"
echo "    $MX submit ${OUT_BASE}/hca/${STUDY_ROOT}/1_cross_node/sram32"
echo "    $MX submit ${OUT_BASE}/hca/${STUDY_ROOT}/1_cross_node/mram14"
echo "    $MX submit ${OUT_BASE}/hca/${STUDY_ROOT}/1_cross_node/mram32"
echo "    $MX submit ${OUT_BASE}/hca/${STUDY_ROOT}/2_static_policy"
echo "    $MX submit ${OUT_BASE}/hca/${STUDY_ROOT}/3_migration_sweep"
echo "    $MX submit ${OUT_BASE}/hca/${STUDY_ROOT}/4_latency_sweep/lat_2x"
echo "    $MX submit ${OUT_BASE}/hca/${STUDY_ROOT}/4_latency_sweep/lat_3x"
echo "    $MX submit ${OUT_BASE}/hca/${STUDY_ROOT}/4_latency_sweep/lat_4x"
echo "    $MX submit ${OUT_BASE}/hca/${STUDY_ROOT}/4_latency_sweep/lat_5x"
echo "    $MX submit ${OUT_BASE}/hca/${STUDY_ROOT}/5_focused_latency_sweep/lat_2x"
echo "    $MX submit ${OUT_BASE}/hca/${STUDY_ROOT}/5_focused_latency_sweep/lat_3x"
echo "    $MX submit ${OUT_BASE}/hca/${STUDY_ROOT}/5_focused_latency_sweep/lat_4x"
echo "    $MX submit ${OUT_BASE}/hca/${STUDY_ROOT}/5_focused_latency_sweep/lat_5x"
echo ""
echo "  Or verify after completion:"
echo "    $MX verify ${OUT_BASE}/hca/${STUDY_ROOT}/1_cross_node/sram7"
echo "    ..."
