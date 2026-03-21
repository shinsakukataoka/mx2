#!/bin/bash
# Fit separate PLMs for each (core_count × cache_size) and validate.
# Filters n=4/n=8 to the 5 clean workloads.

set -euo pipefail

CAL=~/COSC_498/miniMXE/results_test/plm_calibrate
FIT=~/COSC_498/miniMXE/mx2/tools/mcpat_plm_fit.py
SNIPER=~/src/sniper
SUMMARY="$CAL/per_core_plm_summary.txt"

# Clean workloads for n=4 and n=8
N4_CLEAN=(
  "502.gcc_r+502.gcc_r+502.gcc_r+502.gcc_r"
  "505.mcf_r+500.perlbench_r+648.exchange2_s+649.fotonik3d_s"
  "505.mcf_r+505.mcf_r+502.gcc_r+502.gcc_r"
  "505.mcf_r+505.mcf_r+505.mcf_r+505.mcf_r"
  "523.xalancbmk_r+523.xalancbmk_r+502.gcc_r+502.gcc_r"
)
N8_CLEAN=(
  "502.gcc_r+502.gcc_r+502.gcc_r+502.gcc_r+502.gcc_r+502.gcc_r+502.gcc_r+502.gcc_r"
  "505.mcf_r+505.mcf_r+500.perlbench_r+500.perlbench_r+648.exchange2_s+648.exchange2_s+649.fotonik3d_s+649.fotonik3d_s"
  "505.mcf_r+505.mcf_r+505.mcf_r+505.mcf_r+502.gcc_r+502.gcc_r+502.gcc_r+502.gcc_r"
  "505.mcf_r+505.mcf_r+505.mcf_r+505.mcf_r+505.mcf_r+505.mcf_r+505.mcf_r+505.mcf_r"
  "523.xalancbmk_r+523.xalancbmk_r+523.xalancbmk_r+523.xalancbmk_r+502.gcc_r+502.gcc_r+502.gcc_r+502.gcc_r"
)

filter_csv() {
  local src="$1" dst="$2"
  shift 2
  local benches=("$@")
  # Keep header
  head -1 "$src" > "$dst"
  # Keep only matching benchmarks
  for b in "${benches[@]}"; do
    grep ",$b," "$src" >> "$dst" || true
    # Also match if bench is in 2nd column (csv: run_dir,bench,...)
    grep "^[^,]*,$b," "$src" >> "$dst" 2>/dev/null || true
  done
  # Deduplicate
  sort -u -o "$dst" "$dst"
  # Re-add header at top
  local hdr; hdr=$(head -1 "$src")
  { echo "$hdr"; grep -v "^$hdr$" "$dst"; } > "${dst}.tmp" && mv "${dst}.tmp" "$dst"
}

> "$SUMMARY"

for NC in 1 4 8; do
  for L3 in 16 32 128; do
    CSV="$CAL/plm_calib_sunnycove_n${NC}_${L3}M/runs/oracle_points.csv"
    [[ -f "$CSV" ]] || { echo "[SKIP] n${NC} ${L3}MB: CSV not found"; continue; }

    SUFFIX=""; [[ "$L3" != "32" ]] && SUFFIX="_${L3}M"
    OUT="$CAL/plm_sunnycove_n${NC}_cal${SUFFIX}.sh"

    # Filter for n=4/n=8
    USE_CSV="$CSV"
    if [[ "$NC" -eq 4 ]]; then
      USE_CSV="/tmp/plm_n4_${L3}M_filtered.csv"
      filter_csv "$CSV" "$USE_CSV" "${N4_CLEAN[@]}"
    elif [[ "$NC" -eq 8 ]]; then
      USE_CSV="/tmp/plm_n8_${L3}M_filtered.csv"
      filter_csv "$CSV" "$USE_CSV" "${N8_CLEAN[@]}"
    fi

    n_wl=$(tail -n+2 "$USE_CSV" | cut -d, -f2 | sort -u | wc -l)
    n_pts=$(tail -n+2 "$USE_CSV" | wc -l)
    echo "=============================================="
    echo "  Fitting PLM: n=${NC}, L3=${L3}MB"
    echo "  Workloads: ${n_wl}, Points: ${n_pts}"
    echo "=============================================="

    python3 "$FIT" \
      --csv "$USE_CSV" \
      --sniper-home "$SNIPER" \
      --uarch sunnycove \
      --calib-ncores "$NC" \
      --out "$OUT" \
      --validate-csv "$USE_CSV" \
      --validate-ncores "$NC" \
      2>&1 | tee /tmp/plm_fit_n${NC}_${L3}M.log

    echo "--- n=${NC} L3=${L3}MB (${n_wl} workloads, ${n_pts} points) ---" >> "$SUMMARY"
    grep -iE 'MAE|MAPE|[Bb]ias|points|R²|error' /tmp/plm_fit_n${NC}_${L3}M.log >> "$SUMMARY" 2>/dev/null || true
    echo "" >> "$SUMMARY"
  done
done

echo ""
echo "=============================================="
echo "  All fits complete. Summary:"
echo "=============================================="
cat "$SUMMARY"
echo ""
echo "Summary saved to: $SUMMARY"
