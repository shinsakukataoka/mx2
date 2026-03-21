#!/usr/bin/env bash
# plm_validate.sh — validate n1n4n8 PLM for 32/16/128MB
# Produces two reports per cache size:
#   plm_validation_report_{SIZE}M.txt       (full mcpat_plm_fit.py output)
#   plm_validation_summary_{SIZE}M.txt      (concise parsed table)
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null 2>&1 && pwd)"

OUT=~/COSC_498/miniMXE/results_test/plm_calibrate
SNIPER=~/src/sniper

# =========================================================================
# Clean workload lists for n=4 and n=8 (used for filtering)
# =========================================================================
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

# =========================================================================
# Filter oracle CSV to clean workloads only
# =========================================================================
filter_csv() {
  local src="$1" dst="$2" ncores="$3"
  if [[ "$ncores" == "1" ]]; then
    cp "$src" "$dst"; return
  fi
  head -1 "$src" > "$dst"
  local -n arr="N${ncores}_CLEAN"
  for wl in "${arr[@]}"; do
    grep ",$wl," "$src" >> "$dst" || true
  done
}

# =========================================================================
# Get oracle CSV path, creating filtered version if needed
# =========================================================================
get_csv() {
  local size_mb=$1 ncores=$2
  local base="$OUT/plm_calib_sunnycove_n${ncores}_${size_mb}M/runs"
  local raw="$base/oracle_points.csv"

  if [[ "$ncores" == "1" ]]; then
    echo "$raw"; return
  fi

  local filtered="$base/oracle_points_filtered.csv"
  filter_csv "$raw" "$filtered" "$ncores"
  echo "$filtered"
}

# =========================================================================
# Main loop
# =========================================================================
for L3_MB in 32 16 128; do
  N1=$(get_csv $L3_MB 1)
  N4=$(get_csv $L3_MB 4)
  N8=$(get_csv $L3_MB 8)

  for f in "$N1" "$N4" "$N8"; do
    if [[ ! -f "$f" ]]; then
      echo "[SKIP] Missing $f — skipping ${L3_MB}MB"
      continue 2
    fi
  done

  if [[ "$L3_MB" == "32" ]]; then
    CAL=$OUT/plm_sunnycove_n1n4n8_cal.sh
  else
    CAL=$OUT/plm_sunnycove_n1n4n8_cal_${L3_MB}M.sh
  fi

  REPORT_FULL=$OUT/plm_validation_report_${L3_MB}M.txt
  REPORT_SUMMARY=$OUT/plm_validation_summary_${L3_MB}M.txt

  n1c=$(($(wc -l < "$N1") - 1))
  n4c=$(($(wc -l < "$N4") - 1))
  n8c=$(($(wc -l < "$N8") - 1))

  echo "================================================================"
  echo "  L3=${L3_MB}MB: n1=$n1c n4=$n4c n8=$n8c oracle points"
  echo "================================================================"

  # ---- Full report (tee to file + screen) ----
  {
    echo "================================================================"
    echo "  PLM Validation Report — L3=${L3_MB}MB"
    echo "  $(date -u)"
    echo "  n1: $N1 ($n1c pts)"
    echo "  n4: $N4 ($n4c pts)"
    echo "  n8: $N8 ($n8c pts)"
    echo "  PLM: $CAL"
    echo "================================================================"

    echo ""
    echo "  1) Fit (n1+n4+n8 combined)"
    echo "--------------------------------------------------------------"
    python3 "$REPO_ROOT/mx2/tools/mcpat_plm_fit.py" \
        --csv "$N1" --extra-csv "$N4" "$N8" \
        --sniper-home "$SNIPER" --uarch sunnycove --calib-ncores 1 \
        --out "$CAL"

    for NC in 1 4 8; do
      echo ""
      echo "  2) Validate on n=${NC}"
      echo "--------------------------------------------------------------"
      VCSV=$(get_csv $L3_MB $NC)
      python3 "$REPO_ROOT/mx2/tools/mcpat_plm_fit.py" \
          --csv "$N1" --extra-csv "$N4" "$N8" \
          --sniper-home "$SNIPER" --uarch sunnycove --calib-ncores 1 \
          --validate-csv "$VCSV" --validate-ncores "$NC" \
          --out /dev/null
    done
  } 2>&1 | tee "$REPORT_FULL"

  # ---- Concise summary: parse the full report ----
  python3 - "$REPORT_FULL" "$L3_MB" "$REPORT_SUMMARY" << 'PYEOF'
import re, sys

report_file, l3_mb, out_file = sys.argv[1:4]

with open(report_file) as f:
    text = f.read()

lines = []
def pr(s=""): lines.append(s)

pr(f"PLM Validation Summary — L3={l3_mb}MB")
pr(f"{'='*60}")
pr()

# Parse validation sections: look for "ALL" lines with bias/MAE
# Format: "    ALL            210      +1.562W    2.249W"
# And MAPE from per-frequency lines
# Also look for [PASS]/[WARN] verdict lines

# Parse each "Validate on n=X" section
sections = re.split(r'2\) Validate on n=(\d+)', text)
# sections[0] = before first validate, sections[1] = ncores, sections[2] = content, ...

summary_rows = []
for i in range(1, len(sections) - 1, 2):
    nc = sections[i]
    content = sections[i + 1]

    # Find "ALL" line: bias and MAE
    all_match = re.search(r'ALL\s+(\d+)\s+([\+\-][\d.]+)W\s+([\d.]+)W', content)
    if not all_match:
        continue
    n_pts = int(all_match.group(1))
    bias = all_match.group(2)
    mae = all_match.group(3)

    # Find per-frequency MAPE lines and compute overall
    # Lines like "  2.20     2.20    10     +1.234W    1.234W   4.32%"
    mape_vals = re.findall(r'\s+[\d.]+\s+[\d.]+\s+\d+\s+[\+\-][\d.]+W\s+[\d.]+W\s+([\d.]+)%', content)
    if mape_vals:
        mapes = [float(m) for m in mape_vals]
        mape_avg = sum(mapes) / len(mapes)
        mape_min = min(mapes)
        mape_max = max(mapes)
        mape_str = f"{mape_avg:.1f}%"
        mape_range = f"{mape_min:.1f}-{mape_max:.1f}%"
    else:
        mape_str = "N/A"
        mape_range = "N/A"

    # Find verdict
    verdict_match = re.search(r'\[(PASS|WARN)\]\s+(.*)', content)
    verdict = verdict_match.group(1) if verdict_match else "?"
    detail = verdict_match.group(2).strip() if verdict_match else ""

    summary_rows.append({
        'nc': nc, 'n': n_pts, 'bias': bias, 'mae': mae,
        'mape': mape_str, 'mape_range': mape_range, 'verdict': verdict,
    })

    pr(f"  n={nc}  ({n_pts} points)  [{verdict}]")
    pr(f"    Bias={bias}W  MAE={mae}W  MAPE={mape_str} (range: {mape_range})")
    pr()

pr(f"{'='*60}")
pr(f"  OVERALL SUMMARY — L3={l3_mb}MB")
pr(f"{'='*60}")
pr()
pr(f"  {'':>5s}  {'n':>4s}  {'Bias':>8s}  {'MAE':>7s}  {'MAPE':>7s}  {'Range':>14s}  {'':>6s}")
pr(f"  {'-'*60}")
for r in summary_rows:
    pr(f"  n={r['nc']:>2s}  {r['n']:4d}  {r['bias']:>7s}W  {r['mae']:>6s}W  {r['mape']:>7s}  {r['mape_range']:>14s}  [{r['verdict']}]")
pr()

report = "\n".join(lines)
print(report)
with open(out_file, 'w') as f:
    f.write(report + "\n")
print(f"\n[OK] Summary saved to: {out_file}")
PYEOF

  echo ""
done

echo "All reports:"
for L3_MB in 32 16 128; do
  echo "  $OUT/plm_validation_report_${L3_MB}M.txt"
  echo "  $OUT/plm_validation_summary_${L3_MB}M.txt"
done
