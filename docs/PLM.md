LeakDVFS PLM System — Complete Technical Reference
Overview
LeakDVFS exploits the leakage power savings from replacing an SRAM LLC with an MRAM LLC. The saved power is reinvested as higher core frequency via a DVFS governor. The system uses a Piecewise Linear Model (PLM) to predict core+uncore power (excluding LLC leakage) at runtime, enabling frequency decisions within a power budget.

1. Power Model (PLM)
Concept
The PLM predicts P_nocache(f, utilization, IPC) — total power excluding LLC leakage.

At each calibrated frequency f, the model is:

plaintext
P_nocache = b_f + a_util_f × U_sum + a_ipc_f × U_sum × IPC
Where:

b_f = frequency-dependent intercept (idle/static core power)
U_sum = sum of per-core utilization across all core slots (0–8)
IPC = instructions per cycle (system aggregate: total_ins / (N_cores × total_cycles))
N_cores = general/total_cores = 8 (always, even for n=4 workloads — see §5)
The model has 21 frequency entries from 2.0 to 4.0 GHz in 0.1 GHz steps. At runtime, the governor looks up the nearest calibrated frequency.

Key Files
File	Purpose
leakage_conversion.cc
Runtime DVFS governor implementation
mcpat_plm_fit.py
Fits PLM from calibration oracle data
extract_oracle_points.sh
Extracts oracle power + utilization from simulation results
Calibration Files (per core count × cache size)

We use **separate PLMs per core-count regime**, but n=1 uses a combined n1+n4 fit (see §9 for rationale):
- **n=1**: n1+n4 combined model (stable, near-zero bias)
- **n=4**: n=4 per-core model
- **n=8**: n=8 per-core model

plaintext
~/COSC_498/miniMXE/results_test/plm_calibrate/
├── plm_sunnycove_n1n4_cal.sh        # n=1 runtime, 32MB (n1+n4 combined)
├── plm_sunnycove_n1n4_cal_16M.sh    # n=1 runtime, 16MB
├── plm_sunnycove_n1n4_cal_128M.sh   # n=1 runtime, 128MB
├── plm_sunnycove_n4_cal.sh          # n=4, 32MB
├── plm_sunnycove_n4_cal_16M.sh      # n=4, 16MB
├── plm_sunnycove_n4_cal_128M.sh     # n=4, 128MB
├── plm_sunnycove_n8_cal.sh          # n=8, 32MB
├── plm_sunnycove_n8_cal_16M.sh      # n=8, 16MB
└── plm_sunnycove_n8_cal_128M.sh     # n=8, 128MB

Fitting scripts:
- n1+n4: `bash mx2/tools/fit_n1n4_plm.sh`
- n4/n8 per-core: `bash mx2/tools/fit_per_core_plm.sh`

Validation data: `results_test/plm_calibrate/plm_model_validation.csv`
2. Calibration Pipeline
Step 1: Run Oracle Simulations
Static-frequency MRAM runs at 21 frequencies × N workloads × {n1, n4, n8}:

bash
# Plan calibration jobs
bash mx2/plm_calibrate_sweep.sh --mode calib --cores {1,4,8} --l3-mb {16,32,128}
# Submit
~/COSC_498/miniMXE/mx2/bin/mx submit <run_dir>
Step 2: Extract Oracle Points
bash
SNIPER_HOME=~/src/sniper ROOT=<run_dir>/runs bash mx2/tools/extract_oracle_points.sh
Produces oracle_points.csv with columns: run_dir, bench, sim_n, f_ghz, U_sum, P_total_W, P_llc_leak_W, x_fU

Step 3: Fit PLM (per core count)
bash
# Fit all 9 models at once:
bash mx2/tools/fit_per_core_plm.sh

# Or fit a single config:
python3 mx2/tools/mcpat_plm_fit.py \
    --csv <n1_oracle.csv> \
    --sniper-home ~/src/sniper --uarch sunnycove --calib-ncores 1 \
    --out plm_sunnycove_n1_cal.sh \
    --validate-csv <n1_oracle.csv> --validate-ncores 1
IMPORTANT

The fit script computes its own U_sum × IPC from sqlite3 data. It does NOT use the x_fU column from the CSV (which is f × U_sum — a different quantity).

Step 4: Validate
bash
bash mx2/tools/plm_validate.sh
Produces full + summary reports per cache size.

Clean Workloads
Some multicore workloads fail (Sniper timeout/crash). These are excluded from calibration:

n=1: 10 workloads (perlbench, gcc, mcf, omnetpp, xalancbmk, deepsjeng, leela, xz, exchange2, fotonik3d)
n=4 clean set (5 workloads): gcc×4, mcf+perl+exc+foto, mcf×2+gcc×2, mcf×4, xalanc×2+gcc×2
n=8 clean set (5 workloads): gcc×8, mcf×2+perl×2+exc×2+foto×2, mcf×4+gcc×4, mcf×8, xalanc×4+gcc×4

Validation Results (models actually deployed)

| Model | Used for | Cache | Points | MAE (W) | MAPE (%) | Bias (W) |
|-------|----------|-------|--------|---------|----------|----------|
| n1+n4 | n=1      | 16MB  | 210    | 1.390   | 3.34     | +0.069   |
| n1+n4 | n=1      | 32MB  | 210    | 1.556   | 3.37     | +0.091   |
| n1+n4 | n=1      | 128MB | 210    | 1.905   | 2.61     | +0.078   |
| n4 per-core | n=4 | 16MB | 101   | 1.412   | 1.99     | ≈0.000   |
| n4 per-core | n=4 | 32MB | 105   | 0.611   | 0.80     | ≈0.000   |
| n4 per-core | n=4 | 128MB| 105   | 0.483   | 0.46     | ≈0.000   |
| n8 per-core | n=8 | 16MB | 105   | 0.881   | 0.79     | ≈0.000   |
| n8 per-core | n=8 | 32MB | 105   | 1.009   | 0.86     | ≈0.000   |
| n8 per-core | n=8 | 128MB| 105   | 0.929   | 0.65     | ≈0.000   |

CSV: `results_test/plm_calibrate/plm_model_validation.csv`

Why n=1 uses n1+n4 combined (not n=1-only): see §9 (multicollinearity).
Why n=4/n=8 use per-core models: well-conditioned, zero bias.

3. Power Cap Computation
Concept
The power cap represents the SRAM power budget at baseline 2.2 GHz. When MRAM replaces SRAM, LLC leakage drops, creating headroom for DVFS boosting.

Math
plaintext
P_total(f, tech) = P_core(f, workload) + P_llc_leak(tech, cache_size)
P_cap = P_sram_total(2.2GHz)
      = P_core(2.2, wl) + P_sram_llc_leak
      = P_mram_oracle(2.2) + (P_sram_llc - P_mram_llc)
      = P_mram_oracle(2.2) + ΔP_leak
Leakage Values (from device YAMLs)
Cache	SRAM leak	MRAM leak	ΔP_leak
16MB	170.9 mW	101.6 mW	69.3 mW
32MB	330.4 mW	94.5 mW	235.9 mW
128MB	899.1 mW	185.8 mW	713.3 mW
Where Caps Are Stored
params.yaml
 — plm_cap_w section, per workload × core count × cache size
Read by 
plm_sweep.sh
 → passed as lc/power_cap_w flag to Sniper
4. DVFS Governor (Runtime)
Location
leakage_conversion.cc

Per-Interval Logic (every 2M instructions)
plaintext
1. Compute per-core utilization:  u[c] = nonidle_delta / dt_delta
2. sum_util = Σ u[c] for c=0..total_cores-1
3. avg_util = sum_util / total_cores
4. ipc_interval = delta_ins / (total_cores × dt_cycles)
5. Look up PLM entry for current avg core frequency
6. P_nocache = b + a_util × (avg_util × total_cores) + a_ipc × (avg_util × total_cores) × ipc
7. P_est = P_nocache + llc_leak_w    ← uses MRAM leakage
8. If P_est < P_cap - hysteresis → step up frequency
   If P_est > P_cap + hysteresis → step down frequency
Key Parameters
Parameter	Value	Config key
Power cap	per-workload	lc/power_cap_w
LLC leakage	MRAM value	lc/llc_leak_w
Hysteresis	0.10 W	lc/hysteresis_w
Freq range	2.2–4.0 GHz	lc/freq/min_ghz, max_ghz
Freq step	0.10 GHz	lc/freq/step_ghz
Interval	2M instructions	lc/periodic_ins
Critical Fix: U_sum Consistency
Line 132 was changed from getApplicationCores() to getInt("general/total_cores") to ensure m_num_app_cores = 8 always. This matches the calibration oracle which iterates over all 8 core entries in sqlite3 (4 active + 4 idle for n=4 workloads).

5. PLM Sweep (Experimental Runs)
Seven Modes

| Mode | Jobs | What it runs |
|------|------|---|
| main | 60 | MRAM + LeakDVFS for all calibrated workloads × {n1,n4,n8} × {16,32,128}MB |
| comparison | 26 | Static lift: n=1 at f=2.3; n=4 per-workload f* (128MB only) |
| sensitivity | 420 | Read-latency + leakage-gap sweeps for n=1/n=4/n=8 × 3 caches × 7 devices |
| counterfactual | 15 | MRAM LLC + SRAM leakage governor → isolates leakage benefit (n=1+n=4, 128MB) |
| tuning | 300 | h × I cross-product: h={0.05,0.10,0.20,0.30,0.40} × I={1M,2M,3M,4M}, n=1+n=4, 32MB |
| cap_sensitivity | 120 | P_cap ± MAE error bars, same configs as main |

Commands
bash
# Plan
bash mx2/plm_sweep.sh --mode {main,comparison,sensitivity,counterfactual,tuning,cap_sensitivity}
# Submit (all or specific array range)
~/COSC_498/miniMXE/mx2/bin/mx submit results_test/plm_sweep/<mode>
~/COSC_498/miniMXE/mx2/bin/mx submit results_test/plm_sweep/<mode> --sbatch="--array=X-Y"

Per-Core-Count PLM Selection
plm_sweep.sh automatically selects the correct PLM calibration file:
- n=1 → `plm_sunnycove_n1n4_cal{_SIZE}.sh` (n1+n4 combined)
- n=4 → `plm_sunnycove_n4_cal{_SIZE}.sh`
- n=8 → `plm_sunnycove_n8_cal{_SIZE}.sh`

Results Location
plaintext
~/COSC_498/miniMXE/results_test/plm_sweep/
├── main/runs/<workload>/n{1,4,8}/l3_{16,32,128}MB/<variant>/
├── comparison/runs/<workload>/n{1,4}/l3_128MB/static_lift_<freq>/
├── sensitivity/runs/<workload>/n{1,4,8}/l3_{16,32,128}MB/<device_variant>/
├── counterfactual/runs/<workload>/n{1,4}/l3_128MB/<variant>/
├── tuning/runs/<workload>/n{1,4}/l3_32MB/<variant>/
└── cap_sensitivity/runs/<workload>/n{1,4,8}/l3_{16,32,128}MB/<variant>/
6. Static Lift (Comparison Study)
Concept
f* = max frequency where MRAM total power stays within the SRAM power budget, across ALL benchmarks (workload-agnostic):

plaintext
f*_conservative = min over all benchmarks { max f : P_mram(f, bench) ≤ P_cap(bench) }
Results
Cache	f*_min	f*_max	Reason
16MB	2.2 GHz	2.2 GHz	ΔP_leak = 69 mW, too small
32MB	2.2 GHz	2.2 GHz	ΔP_leak = 236 mW, still too small
128MB	2.3 GHz	2.4 GHz	ΔP_leak = 713 mW, sufficient
The comparison study runs at 128MB with f=2.3 (conservative f*).

7. Key Gotchas
WARNING

x_fU ≠ U_sum × IPC. The oracle CSV column x_fU = f × U_sum. The PLM uses U_sum × IPC as its interaction predictor. These are different quantities. The fit script extracts IPC from sqlite3 internally — never use x_fU for PLM predictions.

WARNING

total_cores vs getApplicationCores(). Sniper creates 8 core slots even for n=4 runs. The PLM calibration iterates all 8. The runtime governor must also use 8 (general/total_cores), not getApplicationCores() which may return 4.

IMPORTANT

Power cap = SRAM budget, not MRAM. The cap must include the SRAM→MRAM leakage differential: P_cap = P_mram_oracle(2.2) + ΔP_leak. Without this, the governor has zero headroom.

8. File Index

| Path | Description |
|------|---|
| `mx2/config/params.yaml` | Per-workload power caps and model parameters |
| `mx2/config/devices/*.yaml` | Device configs (latency, energy, leak_mw) |
| `mx2/plm_sweep.sh` | Plans PLM sweep jobs (7 modes) |
| `mx2/plm_calibrate_sweep.sh` | Plans calibration jobs |
| `mx2/tools/extract_oracle_points.sh` | Extracts oracle CSV from sim results |
| `mx2/tools/mcpat_plm_fit.py` | Fits PLM model, validates portability |
| `mx2/tools/fit_n1n4_plm.sh` | Fits n1+n4 combined PLMs (used for n=1) |
| `mx2/tools/fit_per_core_plm.sh` | Fits n4/n8 per-core PLMs |
| `mx2/tools/plm_validate.sh` | Runs full validation, produces reports |
| `mx2/engine/flags_common.sh` | Generates Sniper CLI flags from config |
| `~/src/sniper/common/system/leakage_conversion.cc` | Runtime DVFS governor |
| `results_test/plm_calibrate/` | All calibration data, oracle CSVs, PLM .sh files |
| `results_test/plm_calibrate/plm_model_validation.csv` | Paper-ready validation CSV (all deployed models) |
| `results_test/plm_sweep/` | All experimental sweep results |

The model is based on the paper by ETH Zurich: *A Data-Driven Approach to Lightweight DVFS-Aware Counter-Based Power Modeling for Heterogeneous Platforms* by Sergio Mazzola et al.

---

9. Per-Core-Count Model Selection

Why n=1-only PLM fails

Fitting a PLM on n=1 data alone causes severe multicollinearity.
For single-core, U_sum ≈ 1.0 always and IPC varies over a tiny range (~0.15–0.20).
OLS has no leverage to separate b, a_util, and a_ipc, so it assigns extreme compensating coefficients:

| Coefficient (2.2 GHz) | n=1-only fit | Combined n1+n4+n8 | n1+n4 (chosen) |
|---|---|---|---|
| b (intercept)          | **+614 W**   | 41.5 W            | 40.1 W         |
| a_util                 | **−579 W**   | 2.0 W             | 0.6 W          |
| a_ipc                  | +25 W        | 3.6 W             | 7.2 W          |

At the calibration point (U_sum=1.0, IPC≈0.178), all three produce ~40 W.
But at runtime, interval-level U_sum fluctuations (e.g. 0.95 during a cache miss) cause the n=1-only model to swing by **5.7 W per 0.01 ΔU_sum** — enough to trigger wild throttle/boost oscillations.

The validation MAPE (0.65 W) was misleadingly good: it measured full-simulation-average accuracy, not interval-level stability.

Why n1+n4 is the right choice for n=1

Adding n=4 data provides the OLS fit with U_sum variation (range 2.5–4.0), breaking the collinearity.
The resulting model is stable (ΔP per 0.01 ΔU_sum = 0.019 W), has near-zero bias on n=1 data (+0.09 W vs +1.47 W for n1+n4+n8), and a condition number of ~11.

| Model        | MAE (n=1) | MAPE (n=1) | Bias (n=1)  | Stable? |
|---|---|---|---|---|
| n=1 only     | 0.65 W    | 1.39%      | ≈0 W       | ❌ Catastrophically unstable |
| n1+n4+n8     | 2.21 W    | 4.91%      | +1.47 W    | ✅ |
| **n1+n4**    | **1.56 W**| **3.37%**  | **+0.09 W**| ✅ |

Final model assignment:

| Core count | PLM model | Rationale |
|---|---|---|
| n=1 | n1+n4 combined | Stable, near-zero bias |
| n=4 | n=4 per-core   | Well-conditioned (U_sum has natural range) |
| n=8 | n=8 per-core   | Well-conditioned |

---

10. What the Power Cap Actually Is

Our cap is **not** a single platform TDP. It is a **baseline-referenced comparative cap** defined per workload, core-count regime, and cache size:

    P_cap(w, n, C) = P_sram_pkg(w, n, C, 2.2 GHz)

In practice we reconstruct it as:

    P_cap = P_mram_oracle(2.2, wl) + ΔP_leak
    ΔP_leak = P_sram_llc_leak − P_mram_llc_leak

In plain English:

> **"How much package power would this exact workload/configuration consume at baseline 2.2 GHz if the LLC were SRAM?"**

This makes the framework good for **isolating MRAM's LLC leakage savings**, but less "physically real" than a single fixed package cap or hardware TDP.

Physically real:
- MRAM really reduces LLC leakage relative to SRAM.
- That reduction really can create package-level headroom.

Framework/model dependent:
- How much of that headroom is **usable** by DVFS under our cap definition.
- How much uplift appears under our PLM-based controller.
- The exact n=1 vs n=4/n=8 behavior.

The qualitative mechanism is real, but the exact observed benefits are outcomes of the **evaluation framework**: cap definition + PLM + hysteresis + controller bounds.

---

11. Hysteresis Choice

The hysteresis h must be smaller than ΔP_leak for leakage savings to ever trigger boosting.
With the original h = 0.35 W:

| Cache | ΔP_leak | h = 0.35 W | ΔP_leak > h? |
|---|---|---|---|
| 16MB  | 0.069 W | 0.35 W     | ❌ Can never boost |
| 32MB  | 0.236 W | 0.35 W     | ❌ Can never boost |
| 128MB | 0.713 W | 0.35 W     | ✅ Can boost |

Even with a **perfect** PLM, 16 MB and 32 MB would never boost under h = 0.35 W.

We chose **h = 0.10 W** (fixed across all configurations) because:
- It is smaller than ΔP_leak at 32 MB and 128 MB, enabling boost in those regimes.
- A fixed hysteresis is easier to defend than a per-capacity value (keeps the controller policy consistent across the study).
- It is large enough to suppress trivial oscillation from interval noise.

---

12. Why f_min = 2.2 GHz (Baseline)

The MRAM system at baseline 2.2 GHz is **guaranteed** to consume:

    P_mram(2.2) = P_cap − ΔP_leak ≤ P_cap

Therefore 2.2 GHz is provably power-safe by construction. Any throttle below 2.2 GHz would be a PLM model artifact, not a real power concern.

Setting f_min = 2.2 GHz turns the policy into a **boost-only bounded reallocation mechanism**: it can exploit headroom, but it cannot do worse than the fixed-frequency MRAM baseline. This aligns with the paper's real question: **can recovered MRAM leakage be translated into useful performance uplift?**

---

13. Key n=1 / n=4 / n=8 Interpretation

**Single-core (n=1)** is the cleaner test of isolated MRAM leakage savings.

Leakage savings by cache size:
- 16 MB: ~0.069 W — too small for reliable DVFS uplift
- 32 MB: ~0.236 W — marginal, depends on model accuracy
- 128 MB: ~0.713 W — large enough to produce measurable speedup

A good runtime power model can still be too coarse to reliably convert very small leakage-only savings into useful DVFS uplift. This is not "MRAM hurts"; it means **the leakage savings are too small to be usable by this bounded controller** in that regime.

**Multicore (n=4 / n=8)** benefits from an additional source of headroom: **interval-level dynamic slack**. Aggregate core activity varies as some cores stall during memory-bound phases. Estimated power dips below the baseline-referenced cap, and the governor can boost in those windows.

    usable_headroom_multicore ≈ ΔP_leak + runtime_activity_variation
    usable_headroom_singlecore ≈ ΔP_leak only

This explains why multicore can outperform single-core in the framework even though that sounds counterintuitive. It is not "multicore physically has more free power" but rather: **under the baseline-referenced cap and PLM-based control, multicore exposes more opportunities for beneficial reallocation.**

Compact takeaway:

> MRAM leakage savings are real, but their architectural value depends on whether they become usable package-level headroom under a bounded runtime controller. That usability is weak in small-cache single-core regimes and much stronger in larger-capacity and multicore regimes.