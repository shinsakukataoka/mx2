#!/usr/bin/env python3
"""
dvfs_step_power.py — Compute the power cost of one upward DVFS step (+0.1 GHz)
using the calibrated PLM, evaluated on actual interval states visited in DVFS runs.

For each control interval t:
  ΔP_step(t) = P_nocache(f_t + 0.1, U_sum_t, IPC_t) - P_nocache(f_t, U_sum_t, IPC_t)

Data sources:
  - Per-interval: parsed from [LC] DVFS Change [PLM] lines in sniper.log
  - Runs with no DVFS changes: aggregate from sqlite3
  - PLM coefficients: from calibration .sh files

Outputs written to results_test/plm_sweep/main/:
  1. interval_level_step_power.csv
  2. summary_by_capacity.csv
  3. summary_by_capacity_ncores.csv
  4. summary_by_workload_capacity.csv
"""

import csv
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUNS_ROOT = REPO_ROOT / "results_test" / "plm_sweep" / "main" / "runs"
OUT_DIR   = REPO_ROOT / "results_test" / "plm_sweep" / "main"
PLM_BASE  = REPO_ROOT / "results_test" / "plm_calibrate" / "plm_sunnycove"
TOTAL_CORES = 8
F_MAX     = 4.0
F_STEP    = 0.1
BASE_FREQ = 2.2

HEADROOM_W = {16: 0.0693, 32: 0.2359, 128: 0.7133}

# ---------------------------------------------------------------------------
# PLM loader
# ---------------------------------------------------------------------------
def plm_cal_path(ncores: int, capacity_mb: int) -> Path:
    """Return PLM calibration .sh path per the logic in plm_sweep.sh."""
    if ncores == 1:
        base = f"{PLM_BASE}_n1n4_cal"
    else:
        base = f"{PLM_BASE}_n{ncores}_cal"

    if capacity_mb == 32:
        return Path(f"{base}.sh")
    elif capacity_mb == 16:
        return Path(f"{base}_16M.sh")
    elif capacity_mb == 128:
        return Path(f"{base}_128M.sh")
    else:
        raise ValueError(f"Unknown capacity {capacity_mb}")


def parse_plm_sh(path: Path):
    """Parse PLM .sh file → dict mapping f_ghz → (b, a_util, a_ipc)."""
    text = path.read_text()

    def _arr(name):
        m = re.search(rf"{name}=\(\s*(.*?)\)", text, re.DOTALL)
        if not m:
            raise RuntimeError(f"Cannot find {name} in {path}")
        return [float(x) for x in m.group(1).split()]

    fs   = _arr("PLM_F")
    bs   = _arr("PLM_B")
    a_us = _arr("PLM_AUTIL")
    a_is = _arr("PLM_AIPC")

    plm = {}
    for f, b, au, ai in zip(fs, bs, a_us, a_is):
        plm[round(f, 2)] = (b, au, ai)
    return plm


def nearest_plm(plm: dict, f_ghz: float):
    """Return the PLM entry nearest to f_ghz."""
    best_f = min(plm.keys(), key=lambda x: abs(x - f_ghz))
    return plm[best_f]


def eval_plm(plm: dict, f_ghz: float, u_sum: float, ipc: float) -> float:
    """Evaluate P_nocache = b + a_util * U_sum + a_ipc * U_sum * IPC."""
    b, a_u, a_i = nearest_plm(plm, f_ghz)
    return b + a_u * u_sum + a_i * u_sum * ipc


# ---------------------------------------------------------------------------
# Log parser
# ---------------------------------------------------------------------------
_LC_RE = re.compile(
    r"\[LC\] DVFS Change \[PLM\]:"
    r".*?f_lookup=([0-9.]+)GHz"
    r".*?u_sum=([0-9.]+)"
    r"\s+ipc=([0-9.]+)"
)


def parse_dvfs_log(sniper_log: Path):
    """Yield (f_ghz, u_sum, ipc) for each DVFS-change interval.
    
    Uses grep pre-filter for speed on large log files.
    """
    try:
        proc = subprocess.run(
            ["grep", "-F", "[LC] DVFS Change [PLM]", str(sniper_log)],
            capture_output=True, text=True, timeout=120
        )
        lines = proc.stdout.splitlines()
    except Exception:
        # Fallback to Python
        with open(sniper_log) as fh:
            lines = [l for l in fh if "[LC] DVFS Change [PLM]" in l]

    for line in lines:
        m = _LC_RE.search(line)
        if m:
            yield float(m.group(1)), float(m.group(2)), float(m.group(3))


# ---------------------------------------------------------------------------
# Fallback: aggregate from sqlite3 for runs with no DVFS changes
# ---------------------------------------------------------------------------
def aggregate_from_sqlite(db_path: Path):
    """
    Extract run-aggregate (f=base, U_sum, IPC) from sim.stats.sqlite3.
    Returns a single tuple (f_ghz, U_sum, IPC) or None on failure.
    """
    try:
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()

        # Get roi-begin and roi-end prefix IDs
        pfx = {}
        for row in cur.execute("SELECT prefixid, prefixname FROM prefixes"):
            pfx[row[1]] = row[0]

        roi_begin = pfx.get("roi-begin")
        roi_end   = pfx.get("roi-end")
        if roi_begin is None or roi_end is None:
            con.close()
            return None

        def get_stat(obj: str, metric: str, core: int, prefix_id: int):
            cur.execute(
                'SELECT v.value FROM "values" v '
                "JOIN names n ON v.nameid = n.nameid "
                "WHERE n.objectname = ? AND n.metricname = ? "
                "AND v.core = ? AND v.prefixid = ?",
                (obj, metric, core, prefix_id),
            )
            row = cur.fetchone()
            return row[0] if row else None

        # Compute U_sum = Σ (nonidle_delta / elapsed_delta) over all 8 cores
        # Using performance_model elapsed_time and idle_elapsed_time
        total_ins = 0
        u_sum = 0.0
        elapsed_fs = None

        for c in range(TOTAL_CORES):
            et_begin = get_stat("performance_model", "elapsed_time", c, roi_begin)
            et_end   = get_stat("performance_model", "elapsed_time", c, roi_end)
            idle_begin = get_stat("performance_model", "idle_elapsed_time", c, roi_begin)
            idle_end   = get_stat("performance_model", "idle_elapsed_time", c, roi_end)
            ins_begin = get_stat("performance_model", "instruction_count", c, roi_begin)
            ins_end   = get_stat("performance_model", "instruction_count", c, roi_end)

            if et_begin is None or et_end is None:
                continue

            delta_elapsed = et_end - et_begin
            if elapsed_fs is None:
                elapsed_fs = delta_elapsed

            if idle_begin is not None and idle_end is not None:
                delta_idle = idle_end - idle_begin
                delta_nonidle = delta_elapsed - delta_idle
            else:
                delta_nonidle = delta_elapsed

            if delta_elapsed > 0:
                u_sum += delta_nonidle / delta_elapsed

            if ins_begin is not None and ins_end is not None:
                total_ins += ins_end - ins_begin

        con.close()

        if elapsed_fs is None or elapsed_fs <= 0:
            return None

        # IPC = total_ins / (N_cores * total_cycles)
        # total_cycles = elapsed_fs [fs] * f_avg [GHz] * 1e-6
        f_avg = BASE_FREQ  # run never changed freq
        dt_cycles = elapsed_fs * f_avg * 1e-6
        ipc = total_ins / (TOTAL_CORES * dt_cycles) if dt_cycles > 0 else 0.0

        return (BASE_FREQ, u_sum, ipc)
    except Exception as e:
        print(f"  [WARN] sqlite3 fallback failed for {db_path}: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Walk runs
# ---------------------------------------------------------------------------
def discover_runs():
    """
    Yield dicts with keys: workload, ncores, capacity_mb, run_dir.
    """
    if not RUNS_ROOT.is_dir():
        print(f"[ERR] RUNS_ROOT not found: {RUNS_ROOT}", file=sys.stderr)
        sys.exit(1)

    for wl_dir in sorted(RUNS_ROOT.iterdir()):
        if not wl_dir.is_dir():
            continue
        workload = wl_dir.name
        for n_dir in sorted(wl_dir.iterdir()):
            if not n_dir.is_dir():
                continue
            m = re.match(r"n(\d+)", n_dir.name)
            if not m:
                continue
            ncores = int(m.group(1))
            for cap_dir in sorted(n_dir.iterdir()):
                if not cap_dir.is_dir():
                    continue
                cm = re.match(r"l3_(\d+)MB", cap_dir.name)
                if not cm:
                    continue
                capacity_mb = int(cm.group(1))
                # Find the variant directory (there should be exactly one)
                for var_dir in sorted(cap_dir.iterdir()):
                    if not var_dir.is_dir():
                        continue
                    sniper_log = var_dir / "sniper.log"
                    db_file = var_dir / "sim.stats.sqlite3"
                    if sniper_log.exists():
                        yield {
                            "workload": workload,
                            "ncores": ncores,
                            "capacity_mb": capacity_mb,
                            "run_dir": var_dir,
                            "sniper_log": sniper_log,
                            "db_file": db_file,
                        }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("[dvfs_step_power] Scanning runs …")

    # Cache PLMs: (ncores, capacity_mb) → plm dict
    plm_cache = {}

    all_rows = []
    n_runs = 0
    n_skipped = 0
    n_no_dvfs = 0
    n_intervals_total = 0

    for run in discover_runs():
        wl  = run["workload"]
        nc  = run["ncores"]
        cap = run["capacity_mb"]
        key = (nc, cap)

        # Load PLM if not cached
        if key not in plm_cache:
            p = plm_cal_path(nc, cap)
            if not p.exists():
                print(f"  [WARN] PLM not found: {p}, skipping {wl}/n{nc}/{cap}MB",
                      file=sys.stderr)
                n_skipped += 1
                continue
            plm_cache[key] = parse_plm_sh(p)
            print(f"  Loaded PLM: {p.name} ({len(plm_cache[key])} entries)")

        plm = plm_cache[key]
        n_runs += 1

        # Parse DVFS change intervals
        intervals = list(parse_dvfs_log(run["sniper_log"]))

        if not intervals:
            # No DVFS changes — try sqlite3 fallback
            n_no_dvfs += 1
            if run["db_file"].exists():
                agg = aggregate_from_sqlite(run["db_file"])
                if agg is not None:
                    intervals = [agg]
                else:
                    print(f"  [WARN] No intervals and sqlite3 failed: {run['run_dir']}",
                          file=sys.stderr)
                    continue
            else:
                print(f"  [WARN] No intervals and no db: {run['run_dir']}",
                      file=sys.stderr)
                continue

        # Compute ΔP_step for each interval
        for idx, (f_ghz, u_sum, ipc) in enumerate(intervals):
            f_next = round(f_ghz + F_STEP, 2)
            if f_next > F_MAX + 0.001:
                continue  # already at max, skip

            p_now  = eval_plm(plm, f_ghz, u_sum, ipc)
            p_next = eval_plm(plm, f_next, u_sum, ipc)
            delta  = p_next - p_now

            all_rows.append({
                "workload": wl,
                "ncores": nc,
                "capacity_mb": cap,
                "interval_idx": idx,
                "f_ghz": f_ghz,
                "U_sum": u_sum,
                "IPC": ipc,
                "p_now_w": p_now,
                "p_next_w": p_next,
                "delta_p_step_w": delta,
            })
            n_intervals_total += 1

    print(f"\n[dvfs_step_power] Processed {n_runs} runs, "
          f"{n_no_dvfs} had no DVFS changes (used sqlite3 fallback), "
          f"{n_skipped} skipped, {n_intervals_total} total intervals.")

    if not all_rows:
        print("[ERR] No interval data found!", file=sys.stderr)
        sys.exit(1)

    # --- Write interval-level CSV ---
    interval_csv = OUT_DIR / "interval_level_step_power.csv"
    cols = ["workload", "ncores", "capacity_mb", "interval_idx",
            "f_ghz", "U_sum", "IPC", "p_now_w", "p_next_w", "delta_p_step_w"]
    with open(interval_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(all_rows)
    print(f"  Wrote {interval_csv}  ({len(all_rows)} rows)")

    # --- Compute summaries ---
    deltas = np.array([r["delta_p_step_w"] for r in all_rows])

    def summarize(arr, group_label=None, headroom_w=None):
        """Return summary dict."""
        d = {}
        if group_label:
            d.update(group_label)
        d["count"]  = len(arr)
        d["mean"]   = float(np.mean(arr))
        d["median"] = float(np.median(arr))
        d["p10"]    = float(np.percentile(arr, 10))
        d["p90"]    = float(np.percentile(arr, 90))
        d["min"]    = float(np.min(arr))
        d["max"]    = float(np.max(arr))
        if headroom_w and headroom_w > 0:
            d["headroom_w"] = headroom_w
            d["ratio_mean_to_headroom"] = d["mean"] / headroom_w
            d["ratio_median_to_headroom"] = d["median"] / headroom_w
        return d

    # 1) summary_by_capacity
    by_cap_rows = []
    for cap in sorted(set(r["capacity_mb"] for r in all_rows)):
        arr = np.array([r["delta_p_step_w"] for r in all_rows if r["capacity_mb"] == cap])
        by_cap_rows.append(summarize(arr, {"capacity_mb": cap}, HEADROOM_W.get(cap)))
    _write_summary(OUT_DIR / "summary_by_capacity.csv", by_cap_rows,
                   ["capacity_mb"])

    # 2) summary_by_capacity_ncores
    by_cap_nc_rows = []
    combos = sorted(set((r["capacity_mb"], r["ncores"]) for r in all_rows))
    for cap, nc in combos:
        arr = np.array([r["delta_p_step_w"] for r in all_rows
                        if r["capacity_mb"] == cap and r["ncores"] == nc])
        by_cap_nc_rows.append(summarize(arr,
                                        {"capacity_mb": cap, "ncores": nc},
                                        HEADROOM_W.get(cap)))
    _write_summary(OUT_DIR / "summary_by_capacity_ncores.csv", by_cap_nc_rows,
                   ["capacity_mb", "ncores"])

    # 3) summary_by_workload_capacity
    by_wl_cap_rows = []
    wl_combos = sorted(set((r["workload"], r["capacity_mb"]) for r in all_rows))
    for wl, cap in wl_combos:
        arr = np.array([r["delta_p_step_w"] for r in all_rows
                        if r["workload"] == wl and r["capacity_mb"] == cap])
        by_wl_cap_rows.append(summarize(arr,
                                        {"workload": wl, "capacity_mb": cap},
                                        HEADROOM_W.get(cap)))
    _write_summary(OUT_DIR / "summary_by_workload_capacity.csv", by_wl_cap_rows,
                   ["workload", "capacity_mb"])

    print("\n[dvfs_step_power] Done.")


def _write_summary(path, rows, group_cols):
    """Write summary CSV with group columns + stats."""
    if not rows:
        return
    stat_cols = ["count", "mean", "median", "p10", "p90", "min", "max",
                 "headroom_w", "ratio_mean_to_headroom", "ratio_median_to_headroom"]
    # Only include stat_cols that appear in at least one row
    present_stat = [c for c in stat_cols if any(c in r for r in rows)]
    cols = group_cols + present_stat
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            # Round floats
            out = {}
            for c in cols:
                v = r.get(c)
                if isinstance(v, float):
                    out[c] = f"{v:.6f}"
                else:
                    out[c] = v
            w.writerow(out)
    print(f"  Wrote {path}  ({len(rows)} rows)")


if __name__ == "__main__":
    main()
