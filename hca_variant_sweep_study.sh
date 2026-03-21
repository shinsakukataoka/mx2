#!/usr/bin/env bash
set -euo pipefail

# -----------------------
# Fixed paths
# -----------------------
OUT_BASE="$HOME/COSC_498/miniMXE/results_test"
REPO_ROOT="$HOME/COSC_498/miniMXE"
MX="$REPO_ROOT/mx2/bin/mx"

# -----------------------
# Study knobs
# -----------------------
RUN_GROUP="variant_sweep_study"

UARCH="${UARCH:-gainestown}"
CORES="${CORES:-1}"
ROI_M="${ROI_M:-1000}"
WARMUP_M="${WARMUP_M:-200}"

# Default (r1x_w1x) device params
SRAM_TECH="sram14"
MRAM_TECH="mram14"

# All 10 benches excluding lbm (619.lbm_s) and wrf (621.wrf_s)
BENCHES="541.leela_r,531.deepsjeng_r,520.omnetpp_r,648.exchange2_s,505.mcf_r,523.xalancbmk_r,500.perlbench_r,502.gcc_r,557.xz_r,649.fotonik3d_s"

L3_LIST="32,128"

# -----------------------
# 14 variants:
#   2 baselines
#   3 static grid  (set-parity, way-restricted)
#   3 static noparity (canonical, full assoc)
#   3 dynamic mig  (set-parity, way-restricted)
#   3 dynamic noparity (canonical, full assoc)
# -----------------------
VARIANTS="\
baseline_sram_only,\
baseline_mram_only,\
grid_s4_fillmram,\
grid_s8_fillmram,\
grid_s12_fillmram,\
noparity_s4_fillmram,\
noparity_s8_fillmram,\
noparity_s12_fillmram,\
mig_s4_fillmram_p4_c32,\
mig_s8_fillmram_p4_c32,\
mig_s12_fillmram_p4_c32,\
noparity_s4_fillmram_p4_c32,\
noparity_s8_fillmram_p4_c32,\
noparity_s12_fillmram_p4_c32"

# Strip whitespace/newlines from VARIANTS
VARIANTS="${VARIANTS//[$'\n' ]/}"

# -----------------------
# Sanity checks
# -----------------------
[[ -x "$MX" ]] || { echo "[ERR] mx not found: $MX"; exit 1; }

echo "=============================================="
echo " HCA Variant Sweep Study"
echo " Device:   ${SRAM_TECH} / ${MRAM_TECH} (r1x_w1x)"
echo " Benches:  10 (excl. lbm, wrf)"
echo " L3 sizes: ${L3_LIST}"
echo " Variants: 14"
echo " Total jobs: 14 x 10 x 2 = 280"
echo "=============================================="

RUN_ID="${RUN_GROUP}"
RUN_DIR="${OUT_BASE}/hca/${RUN_GROUP}"

"$MX" plan-hca \
  --out "$OUT_BASE" \
  --run-id "$RUN_ID" \
  --uarch "$UARCH" \
  --sram-tech "$SRAM_TECH" \
  --mram-tech "$MRAM_TECH" \
  --tech-tag "sram14_mram14" \
  --benches "$BENCHES" \
  --l3 "$L3_LIST" \
  --cores "$CORES" \
  --roi-m "$ROI_M" \
  --warmup-m "$WARMUP_M" \
  --variants "$VARIANTS"

"$MX" validate "$RUN_DIR"

echo
echo "[OK] planned all runs under: $RUN_DIR"
echo "[OK] total jobs = 14 variants x 10 benches x 2 sizes = 280"
