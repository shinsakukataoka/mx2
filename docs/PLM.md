# LeakDVFS PLM System - Complete Technical Reference

## Overview
LeakDVFS exploits the leakage power savings from replacing an SRAM LLC with an MRAM LLC. The saved power is reinvested as higher core frequency via a DVFS governor. The system uses a Piecewise Linear Model (PLM) to predict core+uncore power (excluding LLC leakage) at runtime, enabling frequency decisions within a power budget.

## 1. Power Model (PLM)

### Concept
The PLM predicts $P_{\text{nocache}}(f, \text{utilization}, \text{IPC})$ - total power excluding LLC leakage.

At each calibrated frequency $f$, the model is:

$$P_{\text{nocache}} = b_f + a_{\text{util\_f}} \times U_{\text{sum}} + a_{\text{ipc\_f}} \times U_{\text{sum}} \times \text{IPC}$$

Where:
* $b_f$ = frequency-dependent intercept (idle/static core power)
* $U_{\text{sum}}$ = sum of per-core utilization across all core slots (0-8)
* $\text{IPC}$ = instructions per cycle (system aggregate: `total_ins / (N_cores * total_cycles)`)
* $N_{\text{cores}}$ = `general/total_cores` = 8 (always, even for n=4 workloads - see §5)

The model has 21 frequency entries from 2.0 to 4.0 GHz in 0.1 GHz steps. At runtime, the governor looks up the nearest calibrated frequency.

### Key Files

| File | Purpose |
| :--- | :--- |
| `leakage_conversion.cc` | Runtime DVFS governor implementation |
| `mcpat_plm_fit.py` | Fits PLM from calibration oracle data |
| `extract_oracle_points.sh` | Extracts oracle power + utilization from simulation results |

### Calibration Files (per core count × cache size)

We use **separate PLMs per core-count regime**, but n=1 uses a combined n1+n4 fit (see §9 for rationale):
* **n=1**: n1+n4 combined model (stable, near-zero bias)
* **n=4**: n=4 per-core model
* **n=8**: n=8 per-core model

```text
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
```

Fitting scripts:
* n1+n4: `bash mx2/tools/fit_n1n4_plm.sh`
* n4/n8 per-core: `bash mx2/tools/fit_per_core_plm.sh`

Validation data: `results_test/plm_calibrate/plm_model_validation.csv`

---

## 2. Calibration Pipeline

### Step 1: Run Oracle Simulations
Static-frequency MRAM runs at 21 frequencies × N workloads × {n1, n4, n8}:

```bash
# Plan calibration jobs
bash mx2/plm_calibrate_sweep.sh --mode calib --cores {1,4,8} --l3-mb {16,32,128}

# Submit
~/COSC_498/miniMXE/mx2/bin/mx submit <run_dir>
```

### Step 2: Extract Oracle Points
```bash
SNIPER_HOME=~/src/sniper ROOT=<run_dir>/runs bash mx2/tools/extract_oracle_points.sh
```
Produces `oracle_points.csv` with columns: `run_dir`, `bench`, `sim_n`, `f_ghz`, `U_sum`, `P_total_W`, `P_llc_leak_W`, `x_fU`

### Step 3: Fit PLM (per core count)
```bash
# Fit all 9 models at once:
bash mx2/tools/fit_per_core_plm.sh

# Or fit a single config:
python3 mx2/tools/mcpat_plm_fit.py \
    --csv <n1_oracle.csv> \
    --sniper-home ~/src/sniper --uarch sunnycove --calib-ncores 1 \
    --out plm_sunnycove_n1_cal.sh \
    --validate-csv <n1_oracle.csv> --validate-ncores 1
```

> **IMPORTANT:** The fit script computes its own `$U_{\text{sum}} \times \text{IPC}$` from sqlite3 data. It does NOT use the `x_fU` column from the CSV (which is `$f \times U_{\text{sum}}$` - a different quantity).

### Step 4: Validate
```bash
bash mx2/tools/plm_validate.sh
```
Produces full + summary reports per cache size.

### Clean Workloads
Some multicore workloads fail (Sniper timeout/crash). These are excluded from calibration:
* **n=1:** 10 workloads (perlbench, gcc, mcf, omnetpp, xalancbmk, deepsjeng, leela, xz, exchange2, fotonik3d)
* **n=4 clean set (5 workloads):** gcc×4, mcf+perl+exc+foto, mcf×2+gcc×2, mcf×4, xalanc×2+gcc×2
* **n=8 clean set (5 workloads):** gcc×8, mcf×2+perl×2+exc×2+foto×2, mcf×4+gcc×4, mcf×8, xalanc×4+gcc×4

### Validation Results (models actually deployed)

| Model | Used for | Cache | Points | MAE (W) | MAPE (%) | Bias (W) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| n1+n4 | n=1 | 16MB | 210 | 1.390 | 3.34 | +0.069 |
| n1+n4 | n=1 | 32MB | 210 | 1.556 | 3.37 | +0.091 |
| n1+n4 | n=1 | 128MB | 210 | 1.905 | 2.61 | +0.078 |
| n4 per-core | n=4 | 16MB | 101 | 1.412 | 1.99 | ≈0.000 |
| n4 per-core | n=4 | 32MB | 105 | 0.611 | 0.80 | ≈0.000 |
| n4 per-core | n=4 | 128MB | 105 | 0.483 | 0.46 | ≈0.000 |
| n8 per-core | n=8 | 16MB | 105 | 0.881 | 0.79 | ≈0.000 |
| n8 per-core | n=8 | 32MB | 105 | 1.009 | 0.86 | ≈0.000 |
| n8 per-core | n=8 | 128MB | 105 | 0.929 | 0.65 | ≈0.000 |

CSV: `results_test/plm_calibrate/plm_model_validation.csv`

Why n=1 uses n1+n4 combined (not n=1-only): see §9 (multicollinearity).
Why n=4/n=8 use per-core models: well-conditioned, zero bias.

---

## 3. Power Cap Computation

### Concept
The power cap represents the SRAM power budget at baseline 2.2 GHz. When MRAM replaces SRAM, LLC leakage drops, creating headroom for DVFS boosting.

### Math
$$P_{\text{total}}(f, \text{tech}) = P_{\text{core}}(f, \text{workload}) + P_{\text{llc\_leak}}(\text{tech}, \text{cache\_size})$$

$$P_{\text{cap}} = P_{\text{sram\_total}}(2.2\text{GHz})$$
$$P_{\text{cap}} = P_{\text{core}}(2.2, wl) + P_{\text{sram\_llc\_leak}}$$
$$P_{\text{cap}} = P_{\text{mram\_oracle}}(2.2) + (P_{\text{sram\_llc}} - P_{\text{mram\_llc}})$$
$$P_{\text{cap}} = P_{\text{mram\_oracle}}(2.2) + \Delta P_{\text{leak}}$$

### Leakage Values (from device YAMLs)

| Cache | SRAM leak | MRAM leak | $\Delta P_{\text{leak}}$ |
| :--- | :--- | :--- | :--- |
| 16MB | 170.9 mW | 101.6 mW | 69.3 mW |
| 32MB | 330.4 mW | 94.5 mW | 235.9 mW |
| 128MB | 899.1 mW | 185.8 mW | 713.3 mW |

### Where Caps Are Stored
`params.yaml` - `plm_cap_w` section, per workload × core count × cache size
Read by `plm_sweep.sh` $\rightarrow$ passed as `lc/power_cap_w` flag to Sniper

---

## 4. DVFS Governor (Runtime)

### Location
`leakage_conversion.cc`

### Per-Interval Logic (every 2M instructions)
1. Compute per-core utilization: $u[c] = \text{nonidle\_delta} / \text{dt\_delta}$
2. `sum_util` = $\Sigma u[c]$ for $c=0..\text{total\_cores}-1$
3. `avg_util` = `sum_util` / `total_cores`
4. `ipc_interval` = `delta_ins` / (`total_cores` $\times$ `dt_cycles`)
5. Look up PLM entry for current avg core frequency
6. $P_{\text{nocache}} = b + a_{\text{util}} \times (\text{avg\_util} \times \text{total\_cores}) + a_{\text{ipc}} \times (\text{avg\_util} \times \text{total\_cores}) \times \text{ipc}$
7. $P_{\text{est}} = P_{\text{nocache}} + \text{llc\_leak\_w}$ $\leftarrow$ uses MRAM leakage
8. If $P_{\text{est}} < P_{\text{cap}} - \text{hysteresis}$ $\rightarrow$ step up frequency
   If $P_{\text{est}} > P_{\text{cap}} + \text{hysteresis}$ $\rightarrow$ step down frequency

### Key Parameters

| Parameter | Value | Config key |
| :--- | :--- | :--- |
| Power cap | per-workload | `lc/power_cap_w` |
| LLC leakage | MRAM value | `lc/llc_leak_w` |
| Hysteresis | 0.10 W | `lc/hysteresis_w` |
| Freq range | 2.2-4.0 GHz | `lc/freq/min_ghz`, `max_ghz` |
| Freq step | 0.10 GHz | `lc/freq/step_ghz` |
| Interval | 2M instructions | `lc/periodic_ins` |

### Critical Fix: $U_{\text{sum}}$ Consistency
Line 132 was changed from `getApplicationCores()` to `getInt("general/total_cores")` to ensure `m_num_app_cores` = 8 always. This matches the calibration oracle which iterates over all 8 core entries in sqlite3 (4 active + 4 idle for n=4 workloads).

---

## 5. PLM Sweep (Experimental Runs)

### Seven Modes

| Mode | Jobs | What it runs |
| :--- | :--- | :--- |
| main | 60 | MRAM + LeakDVFS for all calibrated workloads × {n1,n4,n8} × {16,32,128}MB |
| comparison | 26 | Static lift: n=1 at f=2.3; n=4 per-workload f* (128MB only) |
| sensitivity | 420 | Read-latency + leakage-gap sweeps for n=1/n=4/n=8 × 3 caches × 7 devices |
| counterfactual | 15 | MRAM LLC + SRAM leakage governor $\rightarrow$ isolates leakage benefit (n=1+n=4, 128MB) |
| tuning | 300 | h × I cross-product: h={0.05,0.10,0.20,0.30,0.40} × I={1M,2M,3M,4M}, n=1+n=4, 32MB |
| cap_sensitivity | 120 | $P_{\text{cap}}$ ± MAE error bars, same configs as main |

### Commands
```bash
# Plan
bash mx2/plm_sweep.sh --mode {main,comparison,sensitivity,counterfactual,tuning,cap_sensitivity}

# Submit (all or specific array range)
~/COSC_498/miniMXE/mx2/bin/mx submit results_test/plm_sweep/<mode>
~/COSC_498/miniMXE/mx2/bin/mx submit results_test/plm_sweep/<mode> --sbatch="--array=X-Y"
```

### Per-Core-Count PLM Selection
`plm_sweep.sh` automatically selects the correct PLM calibration file:
* **n=1** $\rightarrow$ `plm_sunnycove_n1n4_cal{_SIZE}.sh` (n1+n4 combined)
* **n=4** $\rightarrow$ `plm_sunnycove_n4_cal{_SIZE}.sh`
* **n=8** $\rightarrow$ `plm_sunnycove_n8_cal{_SIZE}.sh`

### Results Location
```text
~/COSC_498/miniMXE/results_test/plm_sweep/
├── main/runs/<workload>/n{1,4,8}/l3_{16,32,128}MB/<variant>/
├── comparison/runs/<workload>/n{1,4}/l3_128MB/static_lift_<freq>/
├── sensitivity/runs/<workload>/n{1,4,8}/l3_{16,32,128}MB/<device_variant>/
├── counterfactual/runs/<workload>/n{1,4}/l3_128MB/<variant>/
├── tuning/runs/<workload>/n{1,4}/l3_32MB/<variant>/
└── cap_sensitivity/runs/<workload>/n{1,4,8}/l3_{16,32,128}MB/<variant>/
```

---

## 6. Static Lift (Comparison Study)

### Concept
$f^*$ = max frequency where MRAM total power stays within the SRAM power budget, across ALL benchmarks (workload-agnostic):

$$f^*_{\text{conservative}} = \min \text{ over all benchmarks } \{ \max f : P_{\text{mram}}(f, \text{bench}) \le P_{\text{cap}}(\text{bench}) \}$$

### Results

| Cache | $f^*_{\text{min}}$ | $f^*_{\text{max}}$ | Reason |
| :--- | :--- | :--- | :--- |
| 16MB | 2.2 GHz | 2.2 GHz | $\Delta P_{\text{leak}}$ = 69 mW, too small |
| 32MB | 2.2 GHz | 2.2 GHz | $\Delta P_{\text{leak}}$ = 236 mW, still too small |
| 128MB | 2.3 GHz | 2.4 GHz | $\Delta P_{\text{leak}}$ = 713 mW, sufficient |

The comparison study runs at 128MB with f=2.3 (conservative $f^*$).

---

## 7. Key Gotchas

> **WARNING:** `x_fU` $\neq$ `$U_{\text{sum}} \times \text{IPC}$`. The oracle CSV column `x_fU` = `$f \times U_{\text{sum}}$`. The PLM uses `$U_{\text{sum}} \times \text{IPC}$` as its interaction predictor. These are different quantities. The fit script extracts IPC from sqlite3 internally - never use `x_fU` for PLM predictions.

> **WARNING:** `total_cores` vs `getApplicationCores()`. Sniper creates 8 core slots even for n=4 runs. The PLM calibration iterates all 8. The runtime governor must also use 8 (`general/total_cores`), not `getApplicationCores()` which may return 4.

> **IMPORTANT:** Power cap = SRAM budget, not MRAM. The cap must include the SRAM$\rightarrow$MRAM leakage differential: $P_{\text{cap}} = P_{\text{mram\_oracle}}(2.2) + \Delta P_{\text{leak}}$. Without this, the governor has zero headroom.

---

## 8. File Index

| Path | Description |
| :--- | :--- |
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

## 9. Per-Core-Count Model Selection

### Why n=1-only PLM fails
Fitting a PLM on n=1 data alone causes severe multicollinearity.
For single-core, $U_{\text{sum}} \approx 1.0$ always and IPC varies over a tiny range (~0.15-0.20).
OLS has no leverage to separate b, $a_{\text{util}}$, and $a_{\text{ipc}}$, so it assigns extreme compensating coefficients:

| Coefficient (2.2 GHz) | n=1-only fit | Combined n1+n4+n8 | n1+n4 (chosen) |
| :--- | :--- | :--- | :--- |
| b (intercept) | **+614 W** | 41.5 W | 40.1 W |
| $a_{\text{util}}$ | **−579 W** | 2.0 W | 0.6 W |
| $a_{\text{ipc}}$ | +25 W | 3.6 W | 7.2 W |

At the calibration point ($U_{\text{sum}}=1.0$, $\text{IPC} \approx 0.178$), all three produce ~40 W.
But at runtime, interval-level $U_{\text{sum}}$ fluctuations (e.g. 0.95 during a cache miss) cause the n=1-only model to swing by **5.7 W per 0.01 $\Delta U_{\text{sum}}$** - enough to trigger wild throttle/boost oscillations.

The validation MAPE (0.65 W) was misleadingly good: it measured full-simulation-average accuracy, not interval-level stability.

### Why n1+n4 is the right choice for n=1
Adding n=4 data provides the OLS fit with $U_{\text{sum}}$ variation (range 2.5-4.0), breaking the collinearity.
The resulting model is stable ($\Delta P$ per 0.01 $\Delta U_{\text{sum}}$ = 0.019 W), has near-zero bias on n=1 data (+0.09 W vs +1.47 W for n1+n4+n8), and a condition number of ~11.

| Model | MAE (n=1) | MAPE (n=1) | Bias (n=1) | Stable? |
| :--- | :--- | :--- | :--- | :--- |
| n=1 only | 0.65 W | 1.39% | ≈0 W | ❌ Catastrophically unstable |
| n1+n4+n8 | 2.21 W | 4.91% | +1.47 W | ✅ |
| **n1+n4** | **1.56 W** | **3.37%** | **+0.09 W** | ✅ |

### Final model assignment:

| Core count | PLM model | Rationale |
| :--- | :--- | :--- |
| n=1 | n1+n4 combined | Stable, near-zero bias |
| n=4 | n=4 per-core | Well-conditioned ($U_{\text{sum}}$ has natural range) |
| n=8 | n=8 per-core | Well-conditioned |

---

## 10. What the Power Cap Actually Is

Our cap is **not** a single platform TDP. It is a **baseline-referenced comparative cap** defined per workload, core-count regime, and cache size:

$$P_{\text{cap}}(w, n, C) = P_{\text{sram\_pkg}}(w, n, C, 2.2 \text{ GHz})$$

In practice we reconstruct it as:

$$P_{\text{cap}} = P_{\text{mram\_oracle}}(2.2, wl) + \Delta P_{\text{leak}}$$
$$\Delta P_{\text{leak}} = P_{\text{sram\_llc\_leak}} - P_{\text{mram\_llc\_leak}}$$

In plain English:

> **"How much package power would this exact workload/configuration consume at baseline 2.2 GHz if the LLC were SRAM?"**

This makes the framework good for **isolating MRAM's LLC leakage savings**, but less "physically real" than a single fixed package cap or hardware TDP.

**Physically real:**
* MRAM really reduces LLC leakage relative to SRAM.
* That reduction really can create package-level headroom.

**Framework/model dependent:**
* How much of that headroom is **usable** by DVFS under our cap definition.
* How much uplift appears under our PLM-based controller.
* The exact n=1 vs n=4/n=8 behavior.

The qualitative mechanism is real, but the exact observed benefits are outcomes of the **evaluation framework**: cap definition + PLM + hysteresis + controller bounds.

---

## 11. Hysteresis Choice

The hysteresis $h$ must be smaller than $\Delta P_{\text{leak}}$ for leakage savings to ever trigger boosting.
With the original $h$ = 0.35 W:

| Cache | $\Delta P_{\text{leak}}$ | $h$ = 0.35 W | $\Delta P_{\text{leak}} > h$? |
| :--- | :--- | :--- | :--- |
| 16MB | 0.069 W | 0.35 W | ❌ Can never boost |
| 32MB | 0.236 W | 0.35 W | ❌ Can never boost |
| 128MB | 0.713 W | 0.35 W | ✅ Can boost |

Even with a **perfect** PLM, 16 MB and 32 MB would never boost under $h$ = 0.35 W.

We chose **$h$ = 0.10 W** (fixed across all configurations) because:
* It is smaller than $\Delta P_{\text{leak}}$ at 32 MB and 128 MB, enabling boost in those regimes.
* A fixed hysteresis is easier to defend than a per-capacity value (keeps the controller policy consistent across the study).
* It is large enough to suppress trivial oscillation from interval noise.

---

## 12. Why $f_{\text{min}}$ = 2.2 GHz (Baseline)

The MRAM system at baseline 2.2 GHz is **guaranteed** to consume:

$$P_{\text{mram}}(2.2) = P_{\text{cap}} - \Delta P_{\text{leak}} \le P_{\text{cap}}$$

Therefore 2.2 GHz is provably power-safe by construction. Any throttle below 2.2 GHz would be a PLM model artifact, not a real power concern.

Setting $f_{\text{min}}$ = 2.2 GHz turns the policy into a **boost-only bounded reallocation mechanism**: it can exploit headroom, but it cannot do worse than the fixed-frequency MRAM baseline. This aligns with the paper's real question: **can recovered MRAM leakage be translated into useful performance uplift?**

---

## 13. Key n=1 / n=4 / n=8 Interpretation

**Single-core (n=1)** is the cleaner test of isolated MRAM leakage savings.

Leakage savings by cache size:
* 16 MB: ~0.069 W - too small for reliable DVFS uplift
* 32 MB: ~0.236 W - marginal, depends on model accuracy
* 128 MB: ~0.713 W - large enough to produce measurable speedup

A good runtime power model can still be too coarse to reliably convert very small leakage-only savings into useful DVFS uplift. This is not "MRAM hurts"; it means **the leakage savings are too small to be usable by this bounded controller** in that regime.

**Multicore (n=4 / n=8)** benefits from an additional source of headroom: **interval-level dynamic slack**. Aggregate core activity varies as some cores stall during memory-bound phases. Estimated power dips below the baseline-referenced cap, and the governor can boost in those windows.

$$\text{usable\_headroom\_multicore} \approx \Delta P_{\text{leak}} + \text{runtime\_activity\_variation}$$
$$\text{usable\_headroom\_singlecore} \approx \Delta P_{\text{leak}} \text{ only}$$

This explains why multicore can outperform single-core in the framework even though that sounds counterintuitive. It is not "multicore physically has more free power" but rather: **under the baseline-referenced cap and PLM-based control, multicore exposes more opportunities for beneficial reallocation.**

Compact takeaway:

> MRAM leakage savings are real, but their architectural value depends on whether they become usable package-level headroom under a bounded runtime controller. That usability is weak in small-cache single-core regimes and much stronger in larger-capacity and multicore regimes.

---

## 14. Extended Validation & Decision Agreement

### Column Definitions
The following metrics and coefficients are tracked during PLM validation:

| Column | Definition |
| :--- | :--- |
| `model` | Which PLM was used: `n1+n4` (combined), `n4_percore`, `n8_percore` |
| `used_for` | Core count this model is deployed for at runtime (n=1, n=4, n=8) |
| `L3_MB` | LLC capacity (16, 32, 128) |
| `n_points` | Number of oracle data points used for validation |
| `MAE_W` | Mean Absolute Error (watts) - average |
| `MAPE_pct` | Mean Absolute Percentage Error - average |
| `bias_W` | Mean signed error (positive = model overestimates power on average) |
| `b_2p2` | Intercept coefficient at 2.2 GHz |
| `a_util_2p2` | $U_{\text{sum}}$ coefficient at 2.2 GHz |
| `a_ipc_2p2` | $U_{\text{sum}} \times \text{IPC}$ interaction coefficient at 2.2 GHz |
| `sensitivity_dP_per_0p01_dU` | How much $P_{\text{pred}}$ changes per 0.01 change in $U_{\text{sum}}$ (at 2.2 GHz, IPC=0.178) - measures numerical stability |

### Headroom Magnitude
Almost all oracle points have negative headroom - meaning the true power exceeds the cap even at the oracle level:

| Config | $H_{\text{actual}}$ median | $H>0$ | $H>h$ (boostable) |
| :--- | :--- | :--- | :--- |
| 16MB n=1 | -3.12 W | 14% | 10% |
| 32MB n=1 | -3.07 W | 14% | 14% |
| 128MB n=1 | -2.36 W | 20% | 20% |
| 32MB n=4 | -5.79 W | 32% | 31% |
| 32MB n=8 | -14.53 W | 34% | 33% |

This makes sense: the calibration runs sweep frequencies above the 2.2 GHz baseline, so most points are at $f > 2.2$ where power exceeds the cap.

### Decision Agreement
This is the headline metric - does the PLM choose the same DVFS action (boost / hold / down) as the oracle?

| Config | Overall agreement | Boost agree (when oracle says boost) | Down agree (when oracle says down) |
| :--- | :--- | :--- | :--- |
| 16MB n=1 | 88% | 65% (20 pts) | 94% (180 pts) |
| 32MB n=1 | 88% | 60% (30 pts) | 93% (179 pts) |
| 128MB n=1 | 81% | 56% (43 pts) | 89% (165 pts) |
| 32MB n=4 | 94% | 94% (33 pts) | 96% (71 pts) |
| 32MB n=8 | 96% | 94% (35 pts) | 99% (69 pts) |
| 128MB n=8 | 99% | 97% (34 pts) | 100% (71 pts) |

**Key observations:**
* "Down" decisions (throttle) are almost always correct: 89-100% agreement.
* "Boost" decisions are less reliable for n=1 (56-65%), better for multicore (88-97%). These are the rare near-boundary cases where the absolute error matters most.
* "Hold" decisions are nearly always wrong (0-20%), but there are very few of them (0-10 points per group) - the hysteresis band is narrow relative to the signal.
* Overall agreement is 81-99%, highest for multicore where the PLM's per-core models are most accurate.

**New CSVs in `results_test/plm_calibrate/`:**
* `decision_agreement_by_capacity.csv`
* `decision_agreement_by_capacity_ncores.csv`

### Full Overall Agreement Results

| Config | Overall Agreement |
| :--- | :--- |
| 16MB n=1 | 88.1% |
| 16MB n=4 | 90.2% |
| 16MB n=8 | 96.2% |
| 32MB n=1 | 87.6% |
| 32MB n=4 | 94.3% |
| 32MB n=8 | 96.2% |
| 128MB n=1 | 81.4% |
| 128MB n=4 | 96.2% |
| 128MB n=8 | 99.0% |
