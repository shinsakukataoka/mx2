#!/usr/bin/env bash
set -euo pipefail

# -----------------------
# Fixed paths
# -----------------------
OUT_BASE="$HOME/COSC_498/miniMXE/results_test"
REPO_ROOT="$HOME/COSC_498/miniMXE"
MX="$REPO_ROOT/mx2/bin/mx"
DEV_DIR="$REPO_ROOT/mx2/config/devices"

# -----------------------
# Study knobs
# -----------------------
RUN_GROUP="device_sweep_study"

UARCH="${UARCH:-gainestown}"
CORES="${CORES:-1}"
ROI_M="${ROI_M:-1000}"
WARMUP_M="${WARMUP_M:-200}"

BENCHES="520.omnetpp_r,505.mcf_r"
L3_LIST="32,128"

SRAM_TECH="sram14"
BASE_MRAM_TECH="mram14"

VARIANTS="baseline_mram_only,baseline_sram_only,grid_s8_fillmram,mig_s8_fillmram_p4_c32,noparity_s8_fillmram,noparity_s8_fillmram_p4_c32"

# -----------------------
# Sanity checks
# -----------------------
[[ -x "$MX" ]] || { echo "[ERR] missing mx: $MX"; exit 1; }
[[ -f "$DEV_DIR/${BASE_MRAM_TECH}.yaml" ]] || { echo "[ERR] missing base MRAM yaml"; exit 1; }
[[ -f "$DEV_DIR/${SRAM_TECH}.yaml" ]] || { echo "[ERR] missing SRAM yaml"; exit 1; }

mkdir -p "$OUT_BASE/hca/$RUN_GROUP"

# -----------------------
# 1) Generate 25 MRAM tech YAMLs
#    Only rd_cyc / wr_cyc change
# -----------------------
for R in 1 2 3 4 5; do
  for W in 1 2 3 4 5; do
    OUT_YAML="$DEV_DIR/mram14_r${R}x_w${W}x.yaml"

    python3 - "$DEV_DIR/${BASE_MRAM_TECH}.yaml" "$OUT_YAML" "$R" "$W" <<'PY'
import sys, re, pathlib

src = pathlib.Path(sys.argv[1]).read_text().splitlines()
dst = pathlib.Path(sys.argv[2])
r_mult = int(sys.argv[3])
w_mult = int(sys.argv[4])

out = []
for line in src:
    m = re.match(r'^(\s*)rd_cyc:\s*([0-9]+(?:\.[0-9]+)?)\s*$', line)
    if m:
        v = float(m.group(2)) * r_mult
        line = f"{m.group(1)}rd_cyc: {int(v) if v.is_integer() else v}"
    m = re.match(r'^(\s*)wr_cyc:\s*([0-9]+(?:\.[0-9]+)?)\s*$', line)
    if m:
        v = float(m.group(2)) * w_mult
        line = f"{m.group(1)}wr_cyc: {int(v) if v.is_integer() else v}"
    out.append(line)

dst.write_text("\n".join(out) + "\n")
PY

    echo "[OK] wrote $OUT_YAML"
  done
done

# -----------------------
# 2) Plan + validate all 25 points
# -----------------------
for R in 1 2 3 4 5; do
  for W in 1 2 3 4 5; do
    MRAM_TECH="mram14_r${R}x_w${W}x"
    RUN_ID="${RUN_GROUP}/r${R}x_w${W}x"
    RUN_DIR="${OUT_BASE}/hca/${RUN_GROUP}/r${R}x_w${W}x"

    "$MX" plan-hca \
      --out "$OUT_BASE" \
      --run-id "$RUN_ID" \
      --uarch "$UARCH" \
      --sram-tech "$SRAM_TECH" \
      --mram-tech "$MRAM_TECH" \
      --tech-tag "sram14_${MRAM_TECH}" \
      --benches "$BENCHES" \
      --l3 "$L3_LIST" \
      --cores "$CORES" \
      --roi-m "$ROI_M" \
      --warmup-m "$WARMUP_M" \
      --variants "$VARIANTS"

    "$MX" validate "$RUN_DIR"
  done
done

echo
echo "[OK] planned all runs under: $OUT_BASE/hca/$RUN_GROUP"
echo "[OK] total jobs = 25 latency points x 16 jobs each = 400"
