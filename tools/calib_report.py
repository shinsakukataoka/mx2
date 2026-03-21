#!/usr/bin/env python3
"""Report fixed-effect calibration params + per-size power caps.

Reads oracle_points_plus.csv from one or more roots, pools them,
runs the same fixed-effect regression as mcpat_calib_fit.py, and
reports global params + per-size Pcap.

Usage:
    python3 calib_report.py \
        --root DIR1 [DIR2 ...] \
        --device-yaml YAML \
        [--freqs "2.0,2.66,3.2"] \
        [--sizes "32,128"] \
        [--exclude "619.lbm_s,621.wrf_s"] \
        [--u-max 1.05]
"""
import argparse, os, sys, math
import pandas as pd


# --------------- helpers ---------------

def read_leak_w(path):
    """Minimal YAML parser – reads leak_mw entries keyed by integer size."""
    leak_w = {}
    cur = None
    with open(path, "r") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.endswith(":") and s[:-1].isdigit():
                cur = int(s[:-1])
                continue
            if cur is not None and s.startswith("leak_mw:"):
                mw = float(s.split(":", 1)[1].strip())
                leak_w[cur] = mw / 1000.0
                cur = None
    return leak_w


def fit_fixed_effect(points):
    """
    Fixed-effect: y(b,i) = beta_b + m*x(b,i) + eps
    Same algorithm as mcpat_calib_fit.py.fit_fixed_effect.
    Returns dict with b, m, r2 (within-bench), mae, p95, mape, n.
    """
    by = {}
    for b, x, y in points:
        by.setdefault(b, []).append((x, y))

    # within (demeaned) slope
    xs_d, ys_d = [], []
    xbar, ybar = {}, {}
    for b, pts in by.items():
        xb = sum(x for x, _ in pts) / len(pts)
        yb = sum(y for _, y in pts) / len(pts)
        xbar[b] = xb
        ybar[b] = yb
        for x, y in pts:
            xs_d.append(x - xb)
            ys_d.append(y - yb)

    num = sum(x * y for x, y in zip(xs_d, ys_d))
    den = sum(x * x for x in xs_d)
    m = num / den if den != 0 else float("nan")

    # per-bench intercepts
    betas = {b: (ybar[b] - m * xbar[b]) for b in by}
    b_avg = sum(betas.values()) / len(betas)

    # residuals using per-bench intercepts
    resid = []
    ys_all = []
    for b, pts in by.items():
        bb = betas[b]
        for x, y in pts:
            resid.append(y - (bb + m * x))
            ys_all.append(y)

    abs_err = [abs(e) for e in resid]
    abs_err_sorted = sorted(abs_err)
    n = len(abs_err)
    mae = sum(abs_err) / n
    p95 = abs_err_sorted[int(0.95 * (n - 1))]

    # MAPE
    mape_vals = [ae / abs(y) for ae, y in zip(abs_err, ys_all) if abs(y) > 0]
    mape = (sum(mape_vals) / len(mape_vals) * 100) if mape_vals else float("nan")

    # within-bench R² (demeaned)
    yhat_d = [m * x for x in xs_d]
    my = sum(ys_d) / len(ys_d) if ys_d else 0
    ss_res = sum((y - yh) ** 2 for y, yh in zip(ys_d, yhat_d))
    ss_tot = sum((y - my) ** 2 for y in ys_d)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    return {"b": b_avg, "m": m, "r2": r2, "mae": mae, "p95": p95, "mape": mape, "n": n}


# --------------- main ---------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, nargs="+",
                    help="One or more result dirs containing oracle_points_plus.csv")
    ap.add_argument("--device-yaml", required=True)
    ap.add_argument("--freqs", default="")
    ap.add_argument("--sizes", default="32,128",
                    help="Comma-separated LLC sizes (MB) to include (default: 32,128)")
    ap.add_argument("--exclude", default="619.lbm_s,621.wrf_s",
                    help="Comma-separated benches to exclude (default: 619.lbm_s,621.wrf_s)")
    ap.add_argument("--u-max", type=float, default=1.05,
                    help="Drop points with U_sum > u-max (default: 1.05)")
    ap.add_argument("--cap-freq", type=float, default=2.66,
                    help="Frequency (GHz) used for Pcap calculation (default: 2.66)")
    args = ap.parse_args()

    device_yaml = args.device_yaml
    freqs_str = args.freqs.strip()
    exclude = set(s.strip() for s in args.exclude.split(",") if s.strip())
    fit_sizes = [int(s) for s in args.sizes.split(",") if s.strip()]

    # ---- load CSVs ----
    frames = []
    for i, root in enumerate(args.root):
        csv_path = os.path.join(root, "oracle_points_plus.csv")
        if not os.path.exists(csv_path):
            print(f"ERROR: not found: {csv_path}")
            sys.exit(1)
        print(f"[INFO] Loading {csv_path}")
        tmp = pd.read_csv(csv_path)
        tmp["_root_idx"] = i
        frames.append(tmp)

    if not os.path.exists(device_yaml):
        print(f"ERROR: device yaml not found: {device_yaml}")
        sys.exit(1)

    leak_w = read_leak_w(device_yaml)
    df = pd.concat(frames, ignore_index=True)

    # ---- column detection ----
    def pick(cols):
        for c in cols:
            if c in df.columns:
                return c
        return None

    bench_col = pick(["bench", "BENCH"])
    size_col = pick(["size_mb", "SIZE_MB"])
    fcol = pick(["f_ghz", "F_GHZ", "base_freq_ghz", "freq_ghz"])
    xcol = "x_fU"  # always use precomputed x_fU, same as mcpat_calib_fit.py
    ucol = pick(["U_sum", "U", "util"])
    ycol = "P_nocache_W" if "P_nocache_W" in df.columns else "P_total_W"

    for needed, name in [(bench_col, "bench"), (size_col, "size_mb"),
                         (fcol, "f_ghz"), (xcol in df.columns, "x_fU")]:
        if not needed:
            print(f"ERROR: missing column '{name}'. Have: {list(df.columns)}")
            sys.exit(1)

    df[size_col] = df[size_col].astype(int)
    df[xcol] = pd.to_numeric(df[xcol], errors="coerce")
    df[ycol] = pd.to_numeric(df[ycol], errors="coerce")

    # ---- filters ----
    # size filter
    df = df[df[size_col].isin(fit_sizes)]

    # bench exclusion
    if exclude:
        df = df[~df[bench_col].isin(exclude)]

    # u-max filter
    if ucol and ucol in df.columns:
        df[ucol] = pd.to_numeric(df[ucol], errors="coerce")
        df = df[df[ucol] <= args.u_max]

    # freq filter
    freqs = None
    if freqs_str:
        freqs = [float(f) for f in freqs_str.split(",") if f.strip()]
        df[fcol] = pd.to_numeric(df[fcol], errors="coerce")
        df = df[df[fcol].apply(lambda v: any(abs(v - f) < 1e-6 for f in freqs))]

    df = df.dropna(subset=[xcol, ycol, bench_col, size_col])

    if len(df) < 5:
        print("ERROR: too few points after filtering.")
        sys.exit(1)

    sizes = sorted(df[size_col].unique())

    # ---- info ----
    print(f"Roots: {args.root}")
    print(f"Device yaml: {device_yaml}")
    print(f"Sizes in pool: {sizes}")
    print(f"Excluded benches: {exclude or 'none'}")
    print(f"U_max: {args.u_max}")
    if freqs:
        print(f"Freq filter: {freqs}")
    else:
        print("Freq filter: ALL")
    print()

    f_cap = args.cap_freq

    # ============ (A) Pooled global fit ============
    points_all = [(row[bench_col], row[xcol], row[ycol]) for _, row in df.iterrows()]
    fe_pooled = fit_fixed_effect(points_all)

    print("=" * 60)
    print(f"(A) POOLED FIT  (metric={ycol}, {fe_pooled['n']} pts, {len(sizes)} sizes)")
    print("=" * 60)
    print(f"  b_avg  (P_static) = {fe_pooled['b']:.6f} W")
    print(f"  m      (k_dyn)    = {fe_pooled['m']:.6f} W/(GHz*util)")
    print(f"  within-R²         = {fe_pooled['r2']:.6f}")
    print(f"  MAE               = {fe_pooled['mae']:.6f} W")
    print(f"  MAPE              = {fe_pooled['mape']:.2f} %")
    print(f"  p95               = {fe_pooled['p95']:.6f} W")
    print(f"  f_cap             = {f_cap:.2f} GHz")
    print()

    def print_caps(label, b, m):
        print(f"  Per-size caps [{label}]  (Pcap = b_avg + leak + m * f_cap * U)")
        print("  " + "-" * 56)
        for smb in sizes:
            leak = leak_w.get(smb, None)
            n_pts = int((df[size_col] == smb).sum())
            def cap(u, lk=leak):
                if lk is None:
                    return float("nan")
                return b + lk + m * (f_cap * u)
            c1 = cap(1)
            c4 = cap(4)
            leak_s = f"{leak:.6f} W" if leak is not None else "N/A"
            c1_s = f"{c1:.6f} W" if math.isfinite(c1) else "N/A"
            c4_s = f"{c4:.6f} W" if math.isfinite(c4) else "N/A"
            print(f"    size_mb={smb:>4}  (pts={n_pts:>3})  leak={leak_s}  Pcap(U=1)={c1_s}  Pcap(U=4)={c4_s}")
        print()

    print_caps("pooled", fe_pooled["b"], fe_pooled["m"])

    # ============ (B) Per-root fit, then average ============
    if len(args.root) >= 2:
        fe_bs, fe_ms = [], []
        for i, root in enumerate(args.root):
            sub = df[df["_root_idx"] == i]
            pts = [(row[bench_col], row[xcol], row[ycol]) for _, row in sub.iterrows()]
            if len(pts) < 3:
                continue
            fe_i = fit_fixed_effect(pts)
            fe_bs.append(fe_i["b"])
            fe_ms.append(fe_i["m"])
            print(f"  Root {i}: {root}")
            print(f"    b_avg={fe_i['b']:.6f}  m={fe_i['m']:.6f}  R²={fe_i['r2']:.6f}  MAE={fe_i['mae']:.6f}")

        if len(fe_bs) >= 2:
            b_avg_avg = sum(fe_bs) / len(fe_bs)
            m_avg = sum(fe_ms) / len(fe_ms)
            print()
            print("=" * 60)
            print(f"(B) PER-ROOT AVG  (average of {len(fe_bs)} per-root fits)")
            print("=" * 60)
            print(f"  b_avg  (P_static) = {b_avg_avg:.6f} W")
            print(f"  m      (k_dyn)    = {m_avg:.6f} W/(GHz*util)")
            print(f"  f_cap             = {f_cap:.2f} GHz")
            print()
            print_caps("per-root avg", b_avg_avg, m_avg)
    else:
        print("(B) Per-root average: skipped (only 1 root provided)")
        print()


if __name__ == "__main__":
    main()
