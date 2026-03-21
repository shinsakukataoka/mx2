#!/usr/bin/env python3
"""mcpat_plm_fit.py — fit and validate the piecewise-linear power model (PLM).

Model (per calibration frequency f):
    P_nocache(f) = b_f  +  a_util,f * U_sum  +  a_ipc,f * U_sum * ipc_interval

Predictors:
    U_sum         = sum of per-core utilisation fractions (= N_cores * avg_util)
    ipc_interval  = total_ins / (N_cores * total_cycles)   [system-aggregate]
    U_sum*ipc     = interaction: total busy-core compute intensity

Why U_sum (not avg_util):
    U_sum scales with the number of active cores; avg_util does not.
    A model fitted at N=8 predicts the same coefficients as N=4 — the
    difference in P_nocache comes entirely from U_sum being smaller at N=4.
    This portability is an empirical claim; use --validate to check it.

Usage — calibration from a single dataset:
    python3 mcpat_plm_fit.py \\
        --csv /path/to/oracle_points_plus.csv \\
        --sniper-home $SNIPER_HOME \\
        --uarch sunnycove \\
        --calib-ncores 8 \\
        --out plm_sunnycove_cal.sh

Usage — calibration from combined n=1 + n=8 datasets (broader U_sum range):
    python3 mcpat_plm_fit.py \\
        --csv /path/to/n8_oracle_points_plus.csv \\
        --extra-csv /path/to/n1_oracle_points.csv \\
        --sniper-home $SNIPER_HOME \\
        --uarch sunnycove \\
        --calib-ncores 8 \\
        --out plm_sunnycove_cal.sh

Usage — portability validation (needs a second oracle CSV at different N_cores):
    python3 mcpat_plm_fit.py \\
        --csv /path/to/oracle_points_plus.csv \\
        --extra-csv /path/to/n1_oracle_points.csv \\
        --sniper-home $SNIPER_HOME \\
        --uarch sunnycove \\
        --calib-ncores 8 \\
        --validate-csv /path/to/validation_oracle_points.csv \\
        --validate-ncores 4 \\
        --out plm_sunnycove_cal.sh

    The validation section predicts P_nocache for each validation run using
    the fitted coefficients and reports residuals (bias, MAE, MAPE).
    A small systematic bias across all frequencies flags that b_f or the
    dynamic terms have a core-count-dependent component worth noting in
    the paper.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# McPAT helpers
# ---------------------------------------------------------------------------

def run_mcpat(run_dir: Path, sniper_home: Path) -> bool:
    table = run_dir / "mcpat_table.txt"
    if table.exists():
        return True
    tool = sniper_home / "tools" / "mcpat.py"
    r = subprocess.run(
        [sys.executable, str(tool), "-d", str(run_dir), "-t", "total", "-o", "mcpat_total"],
        capture_output=True, text=True, cwd=str(run_dir),
    )
    if r.returncode != 0:
        return False
    table.write_text(r.stdout)
    return True


def parse_mcpat_table(path: Path) -> float:
    with path.open(errors="ignore") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "total":
                return float(parts[1])
    raise RuntimeError(f"No 'total' line in {path}")


# ---------------------------------------------------------------------------
# IPC extraction from sim.stats.sqlite3
# ---------------------------------------------------------------------------

def extract_ipc(run_dir: Path, sniper_home: Path, n_cores: int) -> float:
    """
    System-aggregate IPC from sqlite3 via sniper_lib.

    Matches the definition in leakage_conversion.cc::onPeriodicIns:
        ipc_interval = delta_ins / (N_cores * dt_cycles)
        dt_cycles    = global_time_fs * f_ghz * 1e-6

    Returns per-core average IPC for the full simulation window.
    """
    script = f"""\
import os, sys
sys.path.insert(0, {str(sniper_home / "tools")!r})
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
    r = subprocess.run([sys.executable, "-c", script],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return float(r.stdout.strip())


# ---------------------------------------------------------------------------
# Record loading (shared by calibration and validation paths)
# ---------------------------------------------------------------------------

def load_records(csv_path: Path, sniper_home: Path,
                 n_cores: int, skip_mcpat: bool,
                 label: str = "") -> List[Dict]:
    """
    Read oracle CSV, run McPAT if needed, extract IPC.
    Returns list of dicts with keys:
        bench, f_ghz, U_sum, ipc_interval, u_sum_x_ipc, p_nocache
    """
    rows = list(csv.DictReader(csv_path.open(newline="")))
    if not rows:
        raise SystemExit(f"Empty CSV: {csv_path}")

    prefix = f"[{label}] " if label else ""
    records: List[Dict] = []
    failures = 0

    for row in rows:
        run_dir = Path(row["run_dir"])
        bench   = row.get("bench", "?")
        f_ghz   = float(row["f_ghz"])
        u_sum   = float(row["U_sum"])   # already the sum across cores

        # P_nocache: prefer pre-computed column, then derive from McPAT
        if row.get("P_nocache_W") and row["P_nocache_W"].strip():
            p_nocache = float(row["P_nocache_W"])
        else:
            p_llc_leak = float(row.get("P_llc_leak_W") or "0")
            if not skip_mcpat:
                if not run_mcpat(run_dir, sniper_home):
                    print(f"  {prefix}[FAIL McPAT] {run_dir.name}")
                    failures += 1
                    continue
            table = run_dir / "mcpat_table.txt"
            if not table.exists():
                print(f"  {prefix}[SKIP no mcpat_table] {run_dir.name}")
                failures += 1
                continue
            try:
                p_nocache = parse_mcpat_table(table) - p_llc_leak
            except Exception as e:
                print(f"  {prefix}[FAIL parse mcpat] {run_dir.name}: {e}")
                failures += 1
                continue

        # Sanity filter: drop physically impossible rows
        if p_nocache > 1000.0 or p_nocache < 0.0 or u_sum <= 0.0:
            print(f"  {prefix}[SKIP outlier] {bench} @ {f_ghz:.2f}GHz  "
                  f"U_sum={u_sum:.3f} P_nocache={p_nocache:.1f}W")
            failures += 1
            continue

        # IPC
        if not (run_dir / "sim.stats.sqlite3").exists():
            print(f"  {prefix}[SKIP no sqlite] {run_dir.name}")
            failures += 1
            continue
        try:
            ipc = extract_ipc(run_dir, sniper_home, n_cores)
        except Exception as e:
            print(f"  {prefix}[FAIL IPC] {run_dir.name}: {e}")
            failures += 1
            continue

        u_ipc = u_sum * ipc
        records.append({
            "bench":        bench,
            "f_ghz":        f_ghz,
            "U_sum":        u_sum,
            "ipc_interval": ipc,
            "u_sum_x_ipc":  u_ipc,
            "p_nocache":    p_nocache,
        })
        print(f"  {prefix}{bench:28s} @ {f_ghz:.2f}GHz  "
              f"U_sum={u_sum:.3f}  ipc={ipc:.3f}  "
              f"U_sum*ipc={u_ipc:.3f}  P_nocache={p_nocache:.3f}W")

    if failures:
        print(f"  {prefix}{failures} row(s) skipped due to errors")
    return records


# ---------------------------------------------------------------------------
# OLS fitting
# ---------------------------------------------------------------------------

def fit_ols(X: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, float, float, float]:
    """
    OLS: y = X @ beta via lstsq.
    X columns: [1, U_sum, U_sum*ipc_interval]
    Returns (beta, R², MAE, condition_number).
    """
    beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    yhat  = X @ beta
    resid = y - yhat
    ss_res = float(np.dot(resid, resid))
    ss_tot = float(np.dot(y - y.mean(), y - y.mean()))
    r2  = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else math.nan
    mae = float(np.mean(np.abs(resid)))
    cond = float(np.linalg.cond(X))
    return beta, r2, mae, cond


# ---------------------------------------------------------------------------
# Per-frequency fit
# ---------------------------------------------------------------------------

def fit_per_freq(records: List[Dict]) -> Dict[float, Tuple[float, float, float]]:
    """
    Group records by f_ghz, fit 3-parameter OLS per frequency.
    Returns {f_ghz: (b_f, a_util, a_ipc)}.
    """
    by_freq: Dict[float, List[Dict]] = defaultdict(list)
    for rec in records:
        by_freq[rec["f_ghz"]].append(rec)

    results: Dict[float, Tuple[float, float, float]] = {}
    print(f"\n{'f_GHz':>7}  {'n':>4}  {'R²':>7}  {'MAE':>7}  {'cond':>8}  "
          f"{'b_f':>9}  {'a_util':>9}  {'a_ipc':>9}")
    print("-" * 78)

    for f_ghz in sorted(by_freq):
        pts = by_freq[f_ghz]
        n   = len(pts)
        if n < 3:
            print(f"  {f_ghz:.2f}GHz: only {n} point(s) — need ≥3, skipping")
            continue

        X = np.array([[1.0, p["U_sum"], p["u_sum_x_ipc"]] for p in pts])
        y = np.array([p["p_nocache"] for p in pts])
        beta, r2, mae, cond = fit_ols(X, y)
        b_f, a_util, a_ipc = float(beta[0]), float(beta[1]), float(beta[2])
        results[f_ghz] = (b_f, a_util, a_ipc)

        cond_flag = " *COLLINEAR*" if cond > 30 else ""
        r2_s  = f"{r2:.4f}" if not math.isnan(r2) else "  nan "
        print(f"  {f_ghz:5.2f}  {n:4d}  {r2_s:>7}  {mae:7.3f}W  {cond:8.1f}{cond_flag}"
              f"  {b_f:9.4f}  {a_util:9.4f}  {a_ipc:9.4f}")
        if cond > 30:
            print(f"    NOTE: U_sum and U_sum*ipc may be collinear across the "
                  f"benchmark set at this frequency.\n"
                  f"    Individual a_util / a_ipc estimates are unreliable; "
                  f"their combined prediction is still well-determined.")

    return results


# ---------------------------------------------------------------------------
# Portability validation
# ---------------------------------------------------------------------------

def validate_portability(val_records: List[Dict],
                         fit_results: Dict[float, Tuple[float, float, float]],
                         calib_ncores: int, val_ncores: int) -> None:
    """
    For each validation record find the nearest calibration frequency, predict
    P_nocache, and compare against actual McPAT value.

    Outputs:
      - Per-frequency table: bias (mean signed error), MAE, MAPE
      - Overall summary: bias, MAE, PASS/WARN verdict
      - Residual decomposition: correlation of residuals with U_sum and
        U_sum*ipc, to distinguish an intercept offset (b_f has N-dependent
        component) from a slope error (a_util or a_ipc need separate calib).

    Interpretation guide printed inline:
      - Bias ≈ constant across workloads at same freq, low corr(resid, U_sum):
          → pure intercept offset in b_f; add a scalar correction.
      - Bias grows with U_sum (positive corr):
          → a_util underestimates dynamic power at val_ncores.
      - Bias grows with U_sum*ipc (positive corr, after accounting for U_sum):
          → a_ipc underestimates compute-intensity power.
    """
    print(f"\n{'='*78}")
    print(f"PORTABILITY VALIDATION: calib N={calib_ncores} → deployment N={val_ncores}")
    print(f"  Predictor basis: (U_sum, U_sum*ipc)  — both scale with N_cores")
    print(f"  Null hypothesis: residuals are zero-mean (no N_cores-dependent bias)")
    print(f"{'='*78}")

    freqs_cal = sorted(fit_results)

    def nearest_freq(f: float) -> float:
        return min(freqs_cal, key=lambda fc: abs(fc - f))

    # Augment each validation record with prediction and residual
    all_pts: List[Dict] = []
    by_freq: Dict[float, List[Dict]] = defaultdict(list)
    for rec in val_records:
        fc = nearest_freq(rec["f_ghz"])
        b_f, a_util, a_ipc = fit_results[fc]
        p_pred = b_f + a_util * rec["U_sum"] + a_ipc * rec["u_sum_x_ipc"]
        err    = p_pred - rec["p_nocache"]          # signed: positive = overpredict
        pct    = 100.0 * err / rec["p_nocache"] if rec["p_nocache"] != 0 else math.nan
        pt = {
            "bench":     rec["bench"],
            "f_val":     rec["f_ghz"],
            "cal_f":     fc,
            "p_pred":    p_pred,
            "p_act":     rec["p_nocache"],
            "err":       err,
            "pct":       pct,
            "U_sum":     rec["U_sum"],
            "u_sum_x_ipc": rec["u_sum_x_ipc"],
        }
        by_freq[rec["f_ghz"]].append(pt)
        all_pts.append(pt)

    # Per-frequency summary table
    print(f"\n{'f_val':>7}  {'cal_f':>7}  {'n':>4}  "
          f"{'bias(MSE)':>10}  {'MAE':>7}  {'MAPE':>7}")
    print("-" * 62)
    all_errs: List[float] = []
    for f_val in sorted(by_freq):
        pts  = by_freq[f_val]
        errs = [p["err"] for p in pts]
        pcts = [p["pct"] for p in pts if not math.isnan(p["pct"])]
        bias = sum(errs) / len(errs)
        mae  = sum(abs(e) for e in errs) / len(errs)
        mape = sum(abs(x) for x in pcts) / len(pcts) if pcts else math.nan
        cf   = pts[0]["cal_f"]
        mape_s = f"{mape:6.2f}%" if not math.isnan(mape) else "   nan"
        print(f"  {f_val:5.2f}  {cf:7.2f}  {len(pts):4d}  "
              f"  {bias:+8.3f}W  {mae:7.3f}W  {mape_s}")
        all_errs.extend(errs)

    # Overall summary
    print("-" * 62)
    overall_bias = sum(all_errs) / len(all_errs) if all_errs else math.nan
    overall_mae  = sum(abs(e) for e in all_errs) / len(all_errs) if all_errs else math.nan
    print(f"  {'ALL':>5}  {'':>7}  {len(all_errs):4d}  "
          f"  {overall_bias:+8.3f}W  {overall_mae:7.3f}W")

    # ---- Residual decomposition ----
    # Correlate residuals with U_sum and U_sum*ipc to identify error source.
    print(f"\n[Residual decomposition]")
    errs_arr   = np.array([p["err"]         for p in all_pts])
    usum_arr   = np.array([p["U_sum"]       for p in all_pts])
    u_ipc_arr  = np.array([p["u_sum_x_ipc"] for p in all_pts])

    def pearson(a: np.ndarray, b: np.ndarray) -> float:
        if a.std() < 1e-12 or b.std() < 1e-12:
            return math.nan
        return float(np.corrcoef(a, b)[0, 1])

    corr_usum = pearson(errs_arr, usum_arr)
    corr_uipc = pearson(errs_arr, u_ipc_arr)

    corr_usum_s = f"{corr_usum:+.3f}" if not math.isnan(corr_usum) else "  nan"
    corr_uipc_s = f"{corr_uipc:+.3f}" if not math.isnan(corr_uipc) else "  nan"
    print(f"  corr(residual, U_sum)     = {corr_usum_s}")
    print(f"  corr(residual, U_sum*ipc) = {corr_uipc_s}")
    print()

    # Interpret
    bias_thresh   = 1.0   # W — below this, overall bias is acceptable
    corr_thresh   = 0.5   # Pearson r — above this, correlation is meaningful

    if abs(overall_bias) < bias_thresh:
        verdict = "PASS"
        detail  = (f"bias={overall_bias:+.3f}W  MAE={overall_mae:.3f}W  "
                   f"— model generalises across N_cores.")
    else:
        verdict = "WARN"
        # Diagnose source
        if abs(corr_usum) >= corr_thresh:
            source = (f"Residuals correlate with U_sum (r={corr_usum_s}): "
                      f"a_util has an N_cores-dependent component (slope error). "
                      f"Separate per-N calibrations may be needed.")
        elif abs(corr_uipc) >= corr_thresh:
            source = (f"Residuals correlate with U_sum*ipc (r={corr_uipc_s}): "
                      f"a_ipc has an N_cores-dependent component (slope error).")
        else:
            source = (f"Residuals do not correlate with predictors "
                      f"(r_usum={corr_usum_s}, r_uipc={corr_uipc_s}): "
                      f"likely a constant intercept offset in b_f. "
                      f"A scalar b_f correction of {overall_bias:+.2f}W may suffice.")
        detail = (f"bias={overall_bias:+.3f}W  MAE={overall_mae:.3f}W  — {source}")

    print(f"  [{verdict}]  {detail}")


# ---------------------------------------------------------------------------
# Shell snippet output
# ---------------------------------------------------------------------------

def write_cal_sh(out_path: Path, uarch: str, calib_desc: str,
                 n_records: int, fit_results: Dict[float, Tuple[float, float, float]]) -> None:
    freqs = sorted(fit_results)
    with out_path.open("w") as f:
        f.write("#!/usr/bin/env bash\n")
        f.write(f"# PLM calibration coefficients — {uarch}, calibrated from {calib_desc}\n")
        f.write("# Generated by: mx2/tools/mcpat_plm_fit.py\n")
        f.write("#\n")
        f.write("# Model:  P_nocache(f) = b_f + a_util * U_sum + a_ipc * U_sum * ipc\n")
        f.write("#   U_sum        = Σ per-core util fractions  (= avg_util * N_cores)\n")
        f.write("#   ipc          = total_ins / (N_cores * total_cycles)\n")
        f.write("#   U_sum * ipc  = interaction term (busy-core compute intensity)\n")
        f.write("#\n")
        f.write("# Portability: U_sum and U_sum*ipc both scale with N_cores,\n")
        f.write(f"# so coefficients fitted from {calib_desc} should apply at other N.\n")
        f.write("# Validate with --validate-csv before deploying at a different N.\n")
        f.write("#\n")
        f.write(f"# Calibration: {n_records} runs, {len(freqs)} frequencies, uarch={uarch}\n")
        f.write(f"PLM_N={len(freqs)}\n")
        f.write("PLM_F=(     " + "  ".join(f"{f:6.3f}" for f in freqs) + "  )\n")
        f.write("PLM_B=(     " + "  ".join(f"{fit_results[f][0]:8.4f}" for f in freqs) + "  )\n")
        f.write("PLM_AUTIL=( " + "  ".join(f"{fit_results[f][1]:8.4f}" for f in freqs) + "  )\n")
        f.write("PLM_AIPC=(  " + "  ".join(f"{fit_results[f][2]:8.4f}" for f in freqs) + "  )\n")
    print(f"\n[OK] Wrote: {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--csv", required=True,
                    help="Primary oracle_points_plus.csv from the calibration run set")
    ap.add_argument("--extra-csv", nargs="*", default=[],
                    help="Additional oracle_points CSV files to merge into the "
                         "calibration dataset (e.g. n=1 runs combined with n=8). "
                         "IPC and U_sum are read from each run's own sqlite3/CSV "
                         "data, so mixing core counts is safe.")
    ap.add_argument("--sniper-home", default=os.environ.get("SNIPER_HOME", ""),
                    help="Sniper installation directory (or set SNIPER_HOME)")
    ap.add_argument("--uarch", default="unknown",
                    help="Microarchitecture label (e.g. sunnycove) — "
                         "used in output file header only")
    ap.add_argument("--calib-ncores", type=int, required=True,
                    help="N_cores for the primary calibration CSV "
                         "(used for the output header label; IPC is self-contained "
                         "in the sqlite3 data so extra-csv files work at any N)")
    ap.add_argument("--out", required=True,
                    help="Output shell snippet path (e.g. plm_sunnycove_cal.sh)")
    ap.add_argument("--skip-mcpat", action="store_true",
                    help="Skip McPAT re-run for rows missing mcpat_table.txt")
    # Validation args
    ap.add_argument("--validate-csv",
                    help="oracle_points CSV from validation runs at a different N_cores")
    ap.add_argument("--validate-ncores", type=int,
                    help="N_cores used in the validation runs")
    args = ap.parse_args()

    sniper_home = Path(args.sniper_home).resolve() if args.sniper_home else None
    if not sniper_home or not (sniper_home / "tools" / "mcpat.py").exists():
        raise SystemExit("Cannot find Sniper tools. Set --sniper-home or SNIPER_HOME.")

    csv_path = Path(args.csv).resolve()
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    # ---- Calibration fit ----
    extra_csvs = [Path(p).resolve() for p in (args.extra_csv or [])]
    for ep in extra_csvs:
        if not ep.exists():
            raise SystemExit(f"Extra CSV not found: {ep}")

    n_sources = 1 + len(extra_csvs)
    calib_desc = f"N={args.calib_ncores}" + (f" + {len(extra_csvs)} extra CSV(s)" if extra_csvs else "")
    print(f"[CALIBRATION]  {args.uarch}  {calib_desc}")
    print(f"  Primary CSV: {csv_path}")
    for ep in extra_csvs:
        print(f"  Extra CSV:   {ep}")
    print(f"  Predictors: (1, U_sum, U_sum*ipc_interval)")
    print()
    print("[Loading calibration records ...]")
    cal_records = load_records(csv_path, sniper_home, args.calib_ncores,
                               args.skip_mcpat, label="cal")
    for ep in extra_csvs:
        extra_n = 1   # n_cores comes from sqlite3 data; this arg is unused in extract_ipc
        cal_records += load_records(ep, sniper_home, extra_n,
                                    args.skip_mcpat, label="cal-extra")
    print(f"\n{len(cal_records)} usable calibration points ({n_sources} source(s))")

    if not cal_records:
        raise SystemExit("No usable calibration points.")

    print("\n[Per-frequency OLS fit]")
    fit_results = fit_per_freq(cal_records)

    if not fit_results:
        raise SystemExit("No frequency groups had ≥3 usable points.")

    # ---- Write output ----
    out_path = Path(args.out).resolve()
    write_cal_sh(out_path, args.uarch, calib_desc,
                 len(cal_records), fit_results)

    # ---- Portability validation (optional) ----
    if args.validate_csv:
        if not args.validate_ncores:
            raise SystemExit("--validate-ncores required when --validate-csv is given")
        val_csv = Path(args.validate_csv).resolve()
        if not val_csv.exists():
            raise SystemExit(f"Validation CSV not found: {val_csv}")
        print(f"\n[Loading validation records ...]  N_val={args.validate_ncores}")
        val_records = load_records(val_csv, sniper_home, args.validate_ncores,
                                   args.skip_mcpat, label="val")
        print(f"{len(val_records)} usable validation points")
        if val_records:
            validate_portability(val_records, fit_results,
                                 args.calib_ncores, args.validate_ncores)

    print()
    print("Next: set PLM_CFG_SH to the output path and run the sensitivity sweep.")


if __name__ == "__main__":
    main()
