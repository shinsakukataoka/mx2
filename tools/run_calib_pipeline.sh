ROOT=""
SNIPER_HOME=""
FREQS=""
DEVICE_YAML="$HOME/COSC_498/miniMXE/mx2/config/devices/sram14.yaml"
CAP_FREQ="2.66"
DO_MCPAT=1

# -------- parse args --------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="$2"; shift 2;;
    --sniper-home) SNIPER_HOME="$2"; shift 2;;
    --freqs) FREQS="$2"; shift 2;;
    --device-yaml) DEVICE_YAML="$2"; shift 2;;
    --cap-freq) CAP_FREQ="$2"; shift 2;;
    --no-mcpat) DO_MCPAT=0; shift 1;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1"; usage; exit 1;;
  esac
done

if [[ -z "$ROOT" || -z "$SNIPER_HOME" ]]; then
  echo "ERROR: --root and --sniper-home are required"
  usage
  exit 1
fi

if [[ ! -d "$ROOT" ]]; then
  echo "ERROR: ROOT not found: $ROOT"
  exit 1
fi
if [[ ! -f "$SNIPER_HOME/tools/mcpat.py" ]]; then
  echo "ERROR: mcpat.py not found at: $SNIPER_HOME/tools/mcpat.py"
  exit 1
fi
if [[ ! -f "$DEVICE_YAML" ]]; then
  echo "ERROR: device yaml not found: $DEVICE_YAML"
  exit 1
fi

echo "[INFO] ROOT       = $ROOT"
echo "[INFO] SNIPER_HOME = $SNIPER_HOME"
echo "[INFO] FREQS      = ${FREQS:-(all freqs for reporting)}"
echo "[INFO] DEVICE_YAML= $DEVICE_YAML"
echo

# If oracle_points_plus.csv already exists, ask whether to redo heavy steps
if [ -s "$ROOT/oracle_points_plus.csv" ]; then
  echo "[INFO] Found existing: $ROOT/oracle_points_plus.csv"
  echo "Redo McPAT + CSV rebuild? (y/N)"
  read ans
  case "$ans" in
    y|Y|yes|YES) DO_REBUILD=1 ;;
    *) DO_REBUILD=0 ;;
  esac
else
  DO_REBUILD=1
fi

if [ "$DO_REBUILD" -eq 1 ]; then
  # -------- (1) Re-run McPAT everywhere (in-place) --------
  if [ "$DO_MCPAT" -eq 1 ]; then
    echo "[STEP] Re-running McPAT in all run dirs under ROOT..."
    find "$ROOT" -name sim.stats.sqlite3 -printf '%h\n' | sort -u | while read -r d; do
      echo "[mcpat overwrite xml+table] $d"
      (
        cd "$d" || exit 1
        rm -f mcpat_total.xml mcpat_total.txt mcpat_table.txt mcpat_total.py mcpat_total.png mcpat_rerun.stderr 2>/dev/null || true
        python3 "$SNIPER_HOME/tools/mcpat.py" -d . -t total -o mcpat_total > mcpat_table.txt 2> mcpat_rerun.stderr
      ) || echo "[FAIL] $d"
    done
    echo
  else
    echo "[STEP] Skipping McPAT rerun -- --no-mcpat flag set"
    echo
  fi

  # -------- (2) extract oracle points (writes oracle_points.csv into current dir) --------
  echo "[STEP] extract_oracle_points.sh (writes oracle_points.csv under ROOT)"
  (
    cd "$ROOT" || exit 1
    ROOT="$ROOT" SNIPER_HOME="$SNIPER_HOME" bash "$HOME/COSC_498/miniMXE/mx2/tools/extract_oracle_points.sh"
  ) || { echo "ERROR: extract_oracle_points.sh failed"; exit 1; }
  echo

  # -------- (3) fit + enrich (writes oracle_points_plus.csv under ROOT) --------
  echo "[STEP] mcpat_calib_fit.py -> oracle_points_plus.csv (under ROOT)"
  (
    cd "$ROOT" || exit 1
    python3 "$HOME/COSC_498/miniMXE/mx2/tools/mcpat_calib_fit.py" \
      --root "$ROOT" \
      --sniper-home "$SNIPER_HOME" \
      --oracle oracle_points.csv \
      --out oracle_points_plus.csv \
      --y-domain nocache \
      --fit fixed_effect
  ) || { echo "ERROR: mcpat_calib_fit.py failed"; exit 1; }
  echo
else
  echo "[SKIP] Reusing existing oracle_points_plus.csv; only running report step."
fi

# -------- (4) report fixed-effect params + caps per size_mb --------
echo "[STEP] Report params + caps (U=1 and U=4) per LLC size (uses leak_mw from device yaml)"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/calib_report.py" \
  --root "$ROOT" \
  --device-yaml "$DEVICE_YAML" \
  --freqs "$FREQS" \
  --cap-freq "$CAP_FREQ"