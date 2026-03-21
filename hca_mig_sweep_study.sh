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
RUN_GROUP="mig_sweep_study"

UARCH="${UARCH:-gainestown}"
CORES="${CORES:-1}"
ROI_M="${ROI_M:-1000}"
WARMUP_M="${WARMUP_M:-200}"

SRAM_TECH="sram14"
MRAM_TECH="mram14"

BENCHES="520.omnetpp_r,505.mcf_r"
L3_LIST="32,128"

# -----------------------
# Sweep dimensions
# -----------------------
SRAM_WAYS=(4 8 12)
PROMOTE=(2 4 8 16)
COOLDOWN=(8 16 32 64)

# -----------------------
# Build variant list
# -----------------------
# Baselines
VARIANTS="baseline_sram_only,baseline_mram_only"

# Static noparity (no migration) for each sram_ways
for sw in "${SRAM_WAYS[@]}"; do
  VARIANTS+=",noparity_s${sw}_fillmram"
done

# Migration noparity sweep: sram_ways × promote × cooldown
for sw in "${SRAM_WAYS[@]}"; do
  for p in "${PROMOTE[@]}"; do
    for c in "${COOLDOWN[@]}"; do
      VARIANTS+=",noparity_s${sw}_fillmram_p${p}_c${c}"
    done
  done
done

# Count variants
IFS=',' read -ra VARR <<< "$VARIANTS"
NUM_VARIANTS="${#VARR[@]}"
# 2 baselines + 3 static + (3 × 4 × 4) = 53 variants
# × 2 benches × 2 sizes = 212 jobs

echo "=============================================="
echo " HCA Migration Sweep Study"
echo " Device:     ${SRAM_TECH} / ${MRAM_TECH}"
echo " Benches:    mcf, omnetpp"
echo " L3 sizes:   ${L3_LIST}"
echo " SRAM ways:  ${SRAM_WAYS[*]}"
echo " Promote:    ${PROMOTE[*]}"
echo " Cooldown:   ${COOLDOWN[*]}"
echo " Variants:   ${NUM_VARIANTS}"
echo " Total jobs:  $((NUM_VARIANTS * 2 * 2))"
echo "=============================================="

# -----------------------
# Sanity checks
# -----------------------
[[ -x "$MX" ]] || { echo "[ERR] mx not found: $MX"; exit 1; }

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
echo "[OK] total jobs = ${NUM_VARIANTS} variants x 2 benches x 2 sizes = $((NUM_VARIANTS * 2 * 2))"
