#!/bin/bash
# Fit n1+n4 combined PLMs for each cache size (used for n=1 DVFS runs).
# n=4 data filtered to clean workloads only.
set -euo pipefail

CAL=~/COSC_498/miniMXE/results_test/plm_calibrate
FIT=~/COSC_498/miniMXE/mx2/tools/mcpat_plm_fit.py
SNIPER=~/src/sniper

N4_CLEAN=(
  "502.gcc_r+502.gcc_r+502.gcc_r+502.gcc_r"
  "505.mcf_r+500.perlbench_r+648.exchange2_s+649.fotonik3d_s"
  "505.mcf_r+505.mcf_r+502.gcc_r+502.gcc_r"
  "505.mcf_r+505.mcf_r+505.mcf_r+505.mcf_r"
  "523.xalancbmk_r+523.xalancbmk_r+502.gcc_r+502.gcc_r"
)

filter_csv() {
  local src="$1" dst="$2"; shift 2
  local benches=("$@")
  head -1 "$src" > "$dst"
  for b in "${benches[@]}"; do
    grep "^[^,]*,$b," "$src" >> "$dst" 2>/dev/null || true
  done
  local hdr; hdr=$(head -1 "$src")
  sort -u -o "$dst" "$dst"
  { echo "$hdr"; grep -v "^$hdr$" "$dst"; } > "${dst}.tmp" && mv "${dst}.tmp" "$dst"
}

for L3 in 16 32 128; do
  N1_CSV="$CAL/plm_calib_sunnycove_n1_${L3}M/runs/oracle_points.csv"
  N4_CSV="$CAL/plm_calib_sunnycove_n4_${L3}M/runs/oracle_points.csv"
  [[ -f "$N1_CSV" ]] || { echo "[SKIP] n1 ${L3}MB: CSV not found"; continue; }
  [[ -f "$N4_CSV" ]] || { echo "[SKIP] n4 ${L3}MB: CSV not found"; continue; }

  SUFFIX=""; [[ "$L3" != "32" ]] && SUFFIX="_${L3}M"
  OUT="$CAL/plm_sunnycove_n1n4_cal${SUFFIX}.sh"

  # Filter n=4 to clean workloads
  N4_FILT="/tmp/plm_n4_${L3}M_filtered.csv"
  filter_csv "$N4_CSV" "$N4_FILT" "${N4_CLEAN[@]}"

  n1_pts=$(tail -n+2 "$N1_CSV" | wc -l)
  n4_pts=$(tail -n+2 "$N4_FILT" | wc -l)
  echo "=============================================="
  echo "  Fitting n1+n4 PLM: L3=${L3}MB"
  echo "  n1: ${n1_pts} pts, n4: ${n4_pts} pts"
  echo "=============================================="

  python3 "$FIT" \
    --csv "$N1_CSV" --extra-csv "$N4_FILT" \
    --sniper-home "$SNIPER" \
    --uarch sunnycove \
    --calib-ncores 1 \
    --out "$OUT" \
    --validate-csv "$N1_CSV" \
    --validate-ncores 1 \
    2>&1 | tee /tmp/plm_fit_n1n4_${L3}M.log

  echo ""
done

echo "=============================================="
echo "  Done. Cal files:"
ls -la "$CAL"/plm_sunnycove_n1n4_cal*.sh
echo "=============================================="
