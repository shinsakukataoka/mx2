#!/usr/bin/env python3
"""
plm_error_cancel.py — Check whether PLM prediction errors cancel in power
differences (headroom, one-step) vs absolute power.

For each workload × frequency × capacity, compares three error metrics:
  1. Absolute error:   PLM(f) − P_nocache_actual(f)
  2. Baseline-referenced headroom error:
       [PLM(f) − PLM(2.2)] vs [actual(f) − actual(2.2)]
  3. One-step difference error:
       [PLM(f+0.1) − PLM(f)] vs [actual(f+0.1) − actual(f)]

Uses McPAT-derived oracle ground truth from the calibration runs.

PLM model selection per plm_sweep.sh:
  n=1 → n1n4 combined; n=4 → n4; n=8 → n8

Outputs:
  error_cancellation_by_capacity.csv       — summary by capacity
  error_cancellation_by_capacity_ncores.csv
  error_cancellation_detail.csv            — per workload × freq detail
"""

import csv
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLM_BASE  = REPO_ROOT / "results_test" / "plm_calibrate" / "plm_sunnycove"
CALIB_BASE = REPO_ROOT / "results_test" / "plm_calibrate"
SNIPER_HOME = Path(os.path.expanduser("~/src/sniper"))
OUT_DIR    = REPO_ROOT / "results_test" / "plm_calibrate"
TOTAL_CORES = 8
BASE_FREQ   = 2.2
F_STEP      = 0.1
HYSTERESIS  = 0.10  # W — governor threshold band

# LLC leakage per capacity (from device YAMLs, in W)
LLC_LEAK_SRAM = {16: 0.1709, 32: 0.33043, 128: 0.89908}
LLC_LEAK_MRAM = {16: 0.1016, 32: 0.09450, 128: 0.18580}
DELTA_LEAK    = {c: LLC_LEAK_SRAM[c] - LLC_LEAK_MRAM[c] for c in [16, 32, 128]}

# Clean workload lists for n=4 and n=8 (from plm_validate.sh)
N4_CLEAN = [
    "502.gcc_r+502.gcc_r+502.gcc_r+502.gcc_r",
    "505.mcf_r+500.perlbench_r+648.exchange2_s+649.fotonik3d_s",
    "505.mcf_r+505.mcf_r+502.gcc_r+502.gcc_r",
    "505.mcf_r+505.mcf_r+505.mcf_r+505.mcf_r",
    "523.xalancbmk_r+523.xalancbmk_r+502.gcc_r+502.gcc_r",
]
N8_CLEAN = [
    "502.gcc_r+502.gcc_r+502.gcc_r+502.gcc_r+502.gcc_r+502.gcc_r+502.gcc_r+502.gcc_r",
    "505.mcf_r+505.mcf_r+500.perlbench_r+500.perlbench_r+648.exchange2_s+648.exchange2_s+649.fotonik3d_s+649.fotonik3d_s",
    "505.mcf_r+505.mcf_r+505.mcf_r+505.mcf_r+502.gcc_r+502.gcc_r+502.gcc_r+502.gcc_r",
    "505.mcf_r+505.mcf_r+505.mcf_r+505.mcf_r+505.mcf_r+505.mcf_r+505.mcf_r+505.mcf_r",
    "523.xalancbmk_r+523.xalancbmk_r+523.xalancbmk_r+523.xalancbmk_r+502.gcc_r+502.gcc_r+502.gcc_r+502.gcc_r",
]


# ---------------------------------------------------------------------------
# PLM loader (same as dvfs_step_power.py)
# ---------------------------------------------------------------------------
def plm_cal_path(ncores: int, capacity_mb: int) -> Path:
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
    text = path.read_text()
    def _arr(name):
        m = re.search(rf"{name}=\(\s*(.*?)\)", text, re.DOTALL)
        if not m:
            raise RuntimeError(f"Cannot find {name} in {path}")
        return [float(x) for x in m.group(1).split()]
    fs = _arr("PLM_F"); bs = _arr("PLM_B")
    a_us = _arr("PLM_AUTIL"); a_is = _arr("PLM_AIPC")
    plm = {}
    for f, b, au, ai in zip(fs, bs, a_us, a_is):
        plm[round(f, 2)] = (b, au, ai)
    return plm


def eval_plm(plm: dict, f_ghz: float, u_sum: float, ipc: float) -> float:
    f_key = round(f_ghz, 2)
    if f_key not in plm:
        f_key = min(plm.keys(), key=lambda x: abs(x - f_ghz))
    b, a_u, a_i = plm[f_key]
    return b + a_u * u_sum + a_i * u_sum * ipc


# ---------------------------------------------------------------------------
# IPC extraction — use sniper_lib via subprocess (matches mcpat_plm_fit.py)
#
# Direct sqlite3 queries fail because Sniper's stats storage has quirks:
#   - Only cores with non-zero values get entries (n=1 → 1 core, not 8)
#   - Some stats have no roi-begin values, making deltas incorrect
# The sniper_lib.get_results() function handles all of this correctly.
# ---------------------------------------------------------------------------
SNIPER_TOOLS = str(SNIPER_HOME / "tools")


def extract_ipc(run_dir: Path) -> float:
    """
    System-aggregate IPC via sniper_lib — exact copy of mcpat_plm_fit.py logic.
    """
    script = f"""\
import os, sys
sys.path.insert(0, {SNIPER_TOOLS!r})
import sniper_lib
r = sniper_lib.get_results(resultsdir={str(run_dir)!r}, partial=None)
res = r["results"]
cfg = r.get("config", {{}})
instr         = res.get("performance_model.instruction_count", [])
global_time   = float(res.get("global.time", 0))
f_ghz         = float(cfg.get("perf_model/core/frequency", 2.0))
n_cores_sim   = len(instr)
total_ins     = sum(float(x) for x in instr)
total_cycles  = global_time * f_ghz * 1e-6
ipc = total_ins / (n_cores_sim * total_cycles) if total_cycles > 0 else 0.0
print(f"{{ipc:.10f}}")
"""
    import subprocess
    r = subprocess.run([sys.executable, "-c", script],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    return float(r.stdout.strip())


# ---------------------------------------------------------------------------
# Load oracle points + compute IPC
# ---------------------------------------------------------------------------
def load_oracle(ncores: int, capacity_mb: int):
    """
    Load oracle CSV for given (ncores, capacity_mb).
    Returns list of dicts with: bench, f_ghz, U_sum, ipc, p_nocache_actual.
    """
    csv_path = (CALIB_BASE /
                f"plm_calib_sunnycove_n{ncores}_{capacity_mb}M" /
                "runs" / "oracle_points.csv")
    if not csv_path.exists():
        print(f"  [WARN] Oracle CSV not found: {csv_path}", file=sys.stderr)
        return []

    # Filter to clean workloads for n=4/n=8
    clean_set = None
    if ncores == 4:
        clean_set = set(N4_CLEAN)
    elif ncores == 8:
        clean_set = set(N8_CLEAN)

    rows = list(csv.DictReader(csv_path.open()))
    records = []
    n_skip = 0

    for row in rows:
        bench = row["bench"]
        if clean_set and bench not in clean_set:
            continue

        run_dir = Path(row["run_dir"])
        f_ghz = float(row["f_ghz"])
        u_sum = float(row["U_sum"])
        p_nocache = float(row["y_PminusLLC"])

        try:
            ipc = extract_ipc(run_dir)
        except Exception as e:
            n_skip += 1
            continue

        records.append({
            "bench": bench,
            "f_ghz": round(f_ghz, 2),
            "U_sum": u_sum,
            "ipc": ipc,
            "p_nocache_actual": p_nocache,
        })

    if n_skip:
        print(f"  [INFO] n={ncores}/{capacity_mb}MB: {n_skip} rows skipped (IPC extraction)")
    return records


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------
def main():
    print("[plm_error_cancel] Loading oracle data and computing errors …\n")

    all_detail = []

    for ncores in [1, 4, 8]:
        for cap_mb in [16, 32, 128]:
            plm_path = plm_cal_path(ncores, cap_mb)
            if not plm_path.exists():
                print(f"  [SKIP] PLM not found: {plm_path}")
                continue
            plm = parse_plm_sh(plm_path)
            print(f"  n={ncores}, {cap_mb}MB: PLM={plm_path.name}")

            records = load_oracle(ncores, cap_mb)
            if not records:
                print(f"    No oracle records, skipping")
                continue

            # Group by workload
            by_bench = defaultdict(list)
            for r in records:
                by_bench[r["bench"]].append(r)

            for bench in sorted(by_bench):
                pts = sorted(by_bench[bench], key=lambda r: r["f_ghz"])

                # Build {f_ghz: (actual, predicted)} mapping
                fp = {}
                for r in pts:
                    f = r["f_ghz"]
                    actual = r["p_nocache_actual"]
                    predicted = eval_plm(plm, f, r["U_sum"], r["ipc"])
                    fp[f] = {
                        "actual": actual,
                        "predicted": predicted,
                        "U_sum": r["U_sum"],
                        "ipc": r["ipc"],
                    }

                # Baseline point
                base = fp.get(BASE_FREQ)
                if base is None:
                    continue

                base_err = base["predicted"] - base["actual"]

                # P_cap = P_nocache_oracle(2.2) + LLC_leak_sram
                # P_actual_total(f) = P_nocache_oracle(f) + LLC_leak_mram
                # P_est_total(f)    = PLM(f) + LLC_leak_mram
                llc_sram = LLC_LEAK_SRAM[cap_mb]
                llc_mram = LLC_LEAK_MRAM[cap_mb]
                p_cap = base["actual"] + llc_sram

                for f in sorted(fp.keys()):
                    d = fp[f]
                    abs_err = d["predicted"] - d["actual"]

                    # Baseline-referenced delta error
                    delta_pred = d["predicted"] - base["predicted"]
                    delta_actual = d["actual"] - base["actual"]
                    headroom_err = delta_pred - delta_actual

                    # One-step difference error
                    f_next = round(f + F_STEP, 2)
                    next_pt = fp.get(f_next)
                    if next_pt is not None:
                        step_pred = next_pt["predicted"] - d["predicted"]
                        step_actual = next_pt["actual"] - d["actual"]
                        step_err = step_pred - step_actual
                    else:
                        step_pred = None
                        step_actual = None
                        step_err = None

                    # --- Headroom and decision agreement ---
                    p_actual_total = d["actual"] + llc_mram
                    p_est_total    = d["predicted"] + llc_mram
                    h_actual = p_cap - p_actual_total
                    h_est    = p_cap - p_est_total

                    # Governor decision: boost/hold/down
                    def _decision(p_est, p_cap, h):
                        if p_est < p_cap - h:  return "boost"
                        if p_est > p_cap + h:  return "down"
                        return "hold"

                    dec_oracle = _decision(p_actual_total, p_cap, HYSTERESIS)
                    dec_plm    = _decision(p_est_total,    p_cap, HYSTERESIS)
                    dec_agree  = dec_oracle == dec_plm

                    row = {
                        "workload": bench,
                        "ncores": ncores,
                        "capacity_mb": cap_mb,
                        "f_ghz": f,
                        "U_sum": d["U_sum"],
                        "IPC": d["ipc"],
                        "p_actual_w": d["actual"],
                        "p_predicted_w": d["predicted"],
                        "abs_err_w": abs_err,
                        "base_err_w": base_err,
                        "delta_actual_w": delta_actual,
                        "delta_predicted_w": delta_pred,
                        "headroom_err_w": headroom_err,
                        "step_actual_w": step_actual if step_err is not None else "",
                        "step_predicted_w": step_pred if step_err is not None else "",
                        "step_err_w": step_err if step_err is not None else "",
                        "p_cap_w": p_cap,
                        "h_actual_w": h_actual,
                        "h_est_w": h_est,
                        "dec_oracle": dec_oracle,
                        "dec_plm": dec_plm,
                        "dec_agree": dec_agree,
                    }

                    all_detail.append(row)

    if not all_detail:
        print("[ERR] No data produced!", file=sys.stderr)
        sys.exit(1)

    # --- Write detail CSV ---
    detail_cols = [
        "workload", "ncores", "capacity_mb", "f_ghz", "U_sum", "IPC",
        "p_actual_w", "p_predicted_w", "abs_err_w", "base_err_w",
        "delta_actual_w", "delta_predicted_w", "headroom_err_w",
        "step_actual_w", "step_predicted_w", "step_err_w",
        "p_cap_w", "h_actual_w", "h_est_w",
        "dec_oracle", "dec_plm", "dec_agree",
    ]
    detail_path = OUT_DIR / "error_cancellation_detail.csv"
    with open(detail_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=detail_cols)
        w.writeheader()
        for r in all_detail:
            out = {}
            for c in detail_cols:
                v = r.get(c, "")
                if isinstance(v, float):
                    out[c] = f"{v:.6f}"
                elif isinstance(v, bool):
                    out[c] = str(v)
                else:
                    out[c] = v
            w.writerow(out)
    print(f"\n  Wrote {detail_path} ({len(all_detail)} rows)")

    # --- Compute error cancellation summaries (unchanged) ---
    def compute_summary(rows, group_keys):
        groups = defaultdict(list)
        for r in rows:
            key = tuple(r[k] for k in group_keys)
            groups[key].append(r)

        summaries = []
        for key, grp in sorted(groups.items()):
            abs_errs = np.array([r["abs_err_w"] for r in grp])
            headroom_errs = np.array([r["headroom_err_w"] for r in grp])
            step_errs = np.array([r["step_err_w"] for r in grp
                                  if r["step_err_w"] != ""])

            d = dict(zip(group_keys, key))
            d["n_points"] = len(grp)
            d["abs_bias_w"] = float(np.mean(abs_errs))
            d["abs_mae_w"] = float(np.mean(np.abs(abs_errs)))
            d["abs_rmse_w"] = float(np.sqrt(np.mean(abs_errs**2)))
            d["headroom_bias_w"] = float(np.mean(headroom_errs))
            d["headroom_mae_w"] = float(np.mean(np.abs(headroom_errs)))
            d["headroom_rmse_w"] = float(np.sqrt(np.mean(headroom_errs**2)))
            if len(step_errs) > 0:
                d["step_n"] = len(step_errs)
                d["step_bias_w"] = float(np.mean(step_errs))
                d["step_mae_w"] = float(np.mean(np.abs(step_errs)))
                d["step_rmse_w"] = float(np.sqrt(np.mean(step_errs**2)))
            else:
                d["step_n"] = 0
                d["step_bias_w"] = d["step_mae_w"] = d["step_rmse_w"] = ""
            if d["abs_mae_w"] > 0:
                d["headroom_mae_reduction"] = 1.0 - d["headroom_mae_w"] / d["abs_mae_w"]
                d["step_mae_reduction"] = (1.0 - d["step_mae_w"] / d["abs_mae_w"]
                                           if d["step_mae_w"] != "" else "")
            else:
                d["headroom_mae_reduction"] = d["step_mae_reduction"] = ""
            summaries.append(d)
        return summaries

    by_cap = compute_summary(all_detail, ["capacity_mb"])
    _write_summary(OUT_DIR / "error_cancellation_by_capacity.csv", by_cap, ["capacity_mb"])
    by_cap_nc = compute_summary(all_detail, ["capacity_mb", "ncores"])
    _write_summary(OUT_DIR / "error_cancellation_by_capacity_ncores.csv", by_cap_nc,
                   ["capacity_mb", "ncores"])

    # =====================================================================
    # HEADROOM & DECISION AGREEMENT
    # =====================================================================
    def compute_decision_summary(rows, group_keys):
        groups = defaultdict(list)
        for r in rows:
            key = tuple(r[k] for k in group_keys)
            groups[key].append(r)

        summaries = []
        for key, grp in sorted(groups.items()):
            d = dict(zip(group_keys, key))
            n = len(grp)
            d["n_points"] = n

            h_act = np.array([r["h_actual_w"] for r in grp])
            h_est = np.array([r["h_est_w"] for r in grp])

            # Headroom magnitude
            d["h_actual_mean"] = float(np.mean(h_act))
            d["h_actual_median"] = float(np.median(h_act))
            d["h_actual_p10"] = float(np.percentile(h_act, 10))
            d["h_actual_p90"] = float(np.percentile(h_act, 90))
            d["h_actual_positive_pct"] = float(np.mean(h_act > 0) * 100)
            d["h_actual_gt_h_pct"] = float(np.mean(h_act > HYSTERESIS) * 100)

            # Sign agreement: H_actual and H_est have the same sign
            sign_agree = np.sum(np.sign(h_act) == np.sign(h_est))
            d["sign_agree_pct"] = float(sign_agree / n * 100)

            # Decision agreement
            agree = sum(1 for r in grp if r["dec_agree"])
            d["dec_agree_pct"] = float(agree / n * 100)

            # Breakdown by oracle action
            for action in ["boost", "hold", "down"]:
                subset = [r for r in grp if r["dec_oracle"] == action]
                d[f"oracle_{action}_n"] = len(subset)
                if subset:
                    correct = sum(1 for r in subset if r["dec_plm"] == action)
                    d[f"oracle_{action}_agree_pct"] = float(correct / len(subset) * 100)
                else:
                    d[f"oracle_{action}_agree_pct"] = ""

            summaries.append(d)
        return summaries

    dec_by_cap = compute_decision_summary(all_detail, ["capacity_mb"])
    dec_by_cap_nc = compute_decision_summary(all_detail, ["capacity_mb", "ncores"])

    # Write decision summary CSVs
    dec_cols = ["n_points",
                "h_actual_mean", "h_actual_median", "h_actual_p10", "h_actual_p90",
                "h_actual_positive_pct", "h_actual_gt_h_pct",
                "sign_agree_pct", "dec_agree_pct",
                "oracle_boost_n", "oracle_boost_agree_pct",
                "oracle_hold_n", "oracle_hold_agree_pct",
                "oracle_down_n", "oracle_down_agree_pct"]
    _write_summary(OUT_DIR / "decision_agreement_by_capacity.csv",
                   dec_by_cap, ["capacity_mb"], stat_cols_override=dec_cols)
    _write_summary(OUT_DIR / "decision_agreement_by_capacity_ncores.csv",
                   dec_by_cap_nc, ["capacity_mb", "ncores"], stat_cols_override=dec_cols)

    # --- Print all summary tables ---
    print(f"\n[plm_error_cancel] Done.")

    # Error cancellation table
    print("\n" + "=" * 90)
    print("  ERROR CANCELLATION SUMMARY")
    print("=" * 90)
    print(f"  {'Group':<15} {'n':>5}  "
          f"{'|abs|':>8} {'|Δbase|':>8} {'|Δstep|':>8}  "
          f"{'Δ% base':>8} {'Δ% step':>8}")
    print("-" * 90)
    for r in by_cap_nc:
        hr = r["headroom_mae_reduction"]
        sr = r["step_mae_reduction"]
        hr_s = f"{hr:+.1%}" if isinstance(hr, float) else "N/A"
        sr_s = f"{sr:+.1%}" if isinstance(sr, float) else "N/A"
        step_mae_s = f"{r['step_mae_w']:.4f}" if r['step_mae_w'] != "" else "N/A"
        print(f"  {r['capacity_mb']:>3}MB n={r['ncores']:<2}    {r['n_points']:>5}  "
              f"{r['abs_mae_w']:>8.4f} {r['headroom_mae_w']:>8.4f} {step_mae_s:>8}  "
              f"{hr_s:>8} {sr_s:>8}")

    # Headroom & decision table
    print("\n" + "=" * 100)
    print("  HEADROOM & DECISION AGREEMENT")
    print("=" * 100)
    print(f"  {'Group':<15} {'n':>5}  "
          f"{'H_act med':>9} {'H>0':>5} {'H>h':>5}  "
          f"{'sign%':>6} {'dec%':>6}  "
          f"{'boost':>6} {'bst%':>5} {'hold':>6} {'hld%':>5} {'down':>6} {'dwn%':>5}")
    print("-" * 100)
    for r in dec_by_cap_nc:
        ba = r['oracle_boost_agree_pct']
        ha = r['oracle_hold_agree_pct']
        da = r['oracle_down_agree_pct']
        ba_s = f"{ba:.0f}%" if isinstance(ba, (int, float)) and ba != "" else "--"
        ha_s = f"{ha:.0f}%" if isinstance(ha, (int, float)) and ha != "" else "--"
        da_s = f"{da:.0f}%" if isinstance(da, (int, float)) and da != "" else "--"
        print(f"  {r['capacity_mb']:>3}MB n={r['ncores']:<2}    {r['n_points']:>5}  "
              f"{r['h_actual_median']:>9.3f} {r['h_actual_positive_pct']:>4.0f}% {r['h_actual_gt_h_pct']:>4.0f}%  "
              f"{r['sign_agree_pct']:>5.1f}% {r['dec_agree_pct']:>5.1f}%  "
              f"{r['oracle_boost_n']:>6} {ba_s:>5} {r['oracle_hold_n']:>6} {ha_s:>5} {r['oracle_down_n']:>6} {da_s:>5}")
    print()


def _write_summary(path, rows, group_cols, stat_cols_override=None):
    if not rows:
        return
    if stat_cols_override:
        stat_cols = stat_cols_override
    else:
        stat_cols = [
            "n_points",
            "abs_bias_w", "abs_mae_w", "abs_rmse_w",
            "headroom_bias_w", "headroom_mae_w", "headroom_rmse_w",
            "step_n", "step_bias_w", "step_mae_w", "step_rmse_w",
            "headroom_mae_reduction", "step_mae_reduction",
        ]
    cols = group_cols + stat_cols
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            out = {}
            for c in cols:
                v = r.get(c, "")
                if isinstance(v, float):
                    out[c] = f"{v:.6f}"
                else:
                    out[c] = v
            w.writerow(out)
    print(f"  Wrote {path} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
