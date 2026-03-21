## Directory layout

mx2 source:
- `mx2/bin/mx`                    : CLI (plan/validate/submit/verify)
- `mx2/config/site.yaml`          : per-machine paths (Sniper/SPEC/traces/binaries)
- `mx2/config/params.yaml`        : calibrated caps + (p_static, k_dyn) per uarch
- `mx2/config/devices/*.yaml`     : NVSim tables per TECH (mram14/mram32/sram7)
- `mx2/engine/*.sh`               : self-contained runners (SPEC / traces / microbench / kernel)
- `mx2/runner/*.sbatch|.sh`       : SLURM array wrapper + dispatcher

Outputs (default):
- `results_test/<campaign>/<run_id>/`
  - `env.sh`      : exported site paths used by jobs
  - `jobs.txt`    : 1 job per line as KEY=VAL tokens
  - `slurm/`      : SLURM stdout/stderr logs
  - `runs/`       : Sniper run directories

Each Sniper run directory (`OUTDIR`) contains:
- `run.yaml`, `cmd.info`, `env.caller.dump`, `sniper.log`, `sim.stats.sqlite3`, `sim.out` (if generated)
- `mx2_status.yaml` (done/failed + reason)

---

## Prereqs

You need working installs/paths for:
- Sniper (`SNIPER_HOME/run-sniper`, `SNIPER_HOME/scripts/roi-icount.py`)
- SPEC (only if using SPEC campaign): `SPEC_ROOT/shrc` and pre-generated `run_*` dirs
- Traces (only if using traces campaign): `TRACE_ROOT/*.sift`
- Microbench binaries (only if using microbench): `MICROBENCH_BIN/*`
- Kernel driver (only if using kernel): `BLIS_BIN` (+ optional `BLIS_LIBDIR`)

---

## Config files

### 1) `mx2/config/site.yaml` (machine paths)
This is the only per-user/per-machine file.

Minimum:
- `SNIPER_HOME`
- `CONDA_SQLITE_LIB` (or `CONDA_LIB`)
- SPEC campaign: `SPEC_ROOT`
- traces campaign: `TRACE_ROOT`
- microbench campaign: `MICROBENCH_BIN`
- kernel campaign: `BLIS_BIN` (+ `BLIS_LIBDIR` if needed)

### 2) `mx2/config/params.yaml` (calibration numbers)
Contains:
- per-uarch `p_static_w`, `k_dyn_w_per_ghz_util`
- per-uarch caps `cap_w.single[L3_MB]` and `cap_w.multicore[L3_MB]`

This is the “frozen calibration” file used to generate LeakDVFS variant labels.

### 3) `mx2/config/devices/*.yaml` (NVSim tables)
These define device latencies/energies/leakage for each TECH and L3 size.
The engine loads these tables at runtime (so device numbers are editable without code changes).

Files:
- `mram14.yaml`, `mram32.yaml`, `sram7.yaml`

---

## CLI overview

### Plan commands (create a run directory)
- `mx2/bin/mx plan-spec ...`
- `mx2/bin/mx plan-traces ...`
- `mx2/bin/mx plan-microbench ...`
- `mx2/bin/mx plan-kernel ...`

### Validate planned run
- `mx2/bin/mx validate <run_dir>`

### Submit as SLURM array
- `mx2/bin/mx submit <run_dir> [--dry-run] [--sbatch=...]`

### Verify status of finished/ongoing runs
- `mx2/bin/mx verify <run_dir>`

---

## Common knobs (arguments)

### Core experiment shape
- `--uarch gainestown|sunnycove|...`  (passed to `run-sniper -c`)
- `--tech mram14|mram32|sram7`
- `--l3 2,32,128`
- `--cores 1|4|8`
- `--roi-m <M>` and `--warmup-m <M>`

### Variant sets
- `--variant-set baseline`  -> baseline_sram_only + baseline_mram_only
- `--variant-set leakdvfs`  -> lc_* only
- `--variant-set leakdvfs3` -> lc_* + naive_lc_* + sram_lc_*
- `--variant-set all`       -> baseline + leakdvfs3

### Global vs selective DVFS
- global: `--dvfs global`
- selective: `--dvfs selective --topk K`
  - sets `lc/selective/enabled=true` and `lc/selective/k=K`

### LeakDVFS tuning knobs (encoded into variant label)
Defaults:
- `--target-frac 1.0`
- `--hyst-w 0.35`
- `--fmax-ghz 4.0`
- `--step-ghz 0.15`
- `--ldvfs-periodic-ins 2000000`

Overrides:
- `--static-w <W>` override p_static for label
- `--dyn-w <W>` override k_dyn for label

These are encoded into the variant string like:
`lc_c<cap>_s<static>_d<dyn>_tf<tf>_h<hyst>_f<fmax>_st<step>_pi<period>`

You can recover knob values from:
- `run.yaml` (variant field),
- `cmd.info` (explicit `-g lc/...` flags),
- `sniper.log` (LC init / DVFS change lines).

### Traces only
- `--workloads "<mix1>,<mix2>,..."`  (comma-separated; each mix uses `+` between traces)
- `--fmin-ghz 1.6` (sets `LC_FMIN_GHZ` for traces)

### Microbench only
- `--microbenches llc_read_hit,pointer_chase,gather_scatter`
- `--wss 2,8,32,128,256`  (WSS list in MB)

### Kernel only
- `--blis-sizes 512,1024,1536,2048`
- `--blis-reps 50`

### DRAM directory entries
- `--dir-entries 4194304` (default)
Example:
- `--dir-entries 2097152`

---

## How to pass variables beyond CLI

mx2 generates `jobs.txt` lines with KEY=VAL tokens. The SLURM wrapper exports them.
If you want to add a global knob that isn’t exposed as a CLI flag yet, you can:

1) Edit `<run_dir>/env.sh` to export an env var for all jobs, OR
2) Add `KEY=VAL` to each line in `jobs.txt` (no spaces allowed).

Example: set `LC_FMIN_GHZ=1.6` for SPEC too:
- add `export LC_FMIN_GHZ=1.6` to `env.sh`, or
- append `LC_FMIN_GHZ=1.6` to each job line.

---

## SLURM stdout/stderr location

SLURM logs go to:
- `<run_dir>/slurm/%x-%A_%a.out`
- `<run_dir>/slurm/%x-%A_%a.err`

Sniper logs go to each run’s `OUTDIR/sniper.log`.

---

## Small test runs

```bash
# Plan (creates results_test/spec/<run_id>/...)
mx2/bin/mx plan-spec \
  --uarch gainestown \
  --tech mram14 \
  --benches 505.mcf_r \
  --l3 32 \
  --cores 1 \
  --roi-m 50 --warmup-m 10 \
  --variant-set baseline \
  --tag smoke

# Grab latest run dir
RUN_DIR="$(ls -dt results_test/spec/* | head -1)"

mx2/bin/mx validate "$RUN_DIR"
mx2/bin/mx submit "$RUN_DIR" --dry-run
mx2/bin/mx submit "$RUN_DIR"
mx2/bin/mx verify "$RUN_DIR"
```

```bash
mx2/bin/mx plan-spec \
  --uarch gainestown \
  --tech mram14 \
  --benches 505.mcf_r \
  --l3 32 \
  --cores 1 \
  --roi-m 50 --warmup-m 10 \
  --variant-set leakdvfs3 \
  --tag ldvfs_smoke

RUN_DIR="$(ls -dt results_test/spec/* | head -1)"
mx2/bin/mx validate "$RUN_DIR"
mx2/bin/mx submit "$RUN_DIR"
```

```bash
mx2/bin/mx plan-traces \
  --uarch gainestown \
  --tech mram14 \
  --cores 4 \
  --workloads "505.mcf_r+505.mcf_r+505.mcf_r+505.mcf_r" \
  --l3 32 \
  --roi-m 200 --warmup-m 50 \
  --variant-set leakdvfs \
  --dvfs global \
  --tag trace_smoke

RUN_DIR="$(ls -dt results_test/traces/* | head -1)"
mx2/bin/mx validate "$RUN_DIR"
mx2/bin/mx submit "$RUN_DIR"
```

```bash
mx2/bin/mx plan-traces --uarch gainestown --cores 4 --l3 32 \
  --workloads "505.mcf_r+505.mcf_r+505.mcf_r+505.mcf_r" \
  --variant-set leakdvfs --dvfs selective --topk 2 --tag trace_sel2
```

```bash
mx2/bin/mx plan-microbench \
  --uarch gainestown \
  --tech mram14 \
  --cores 4 \
  --microbenches llc_read_hit \
  --wss 32 \
  --l3 32 \
  --roi-m 50 --warmup-m 0 \
  --variant-set baseline \
  --tag mb_smoke

RUN_DIR="$(ls -dt results_test/microbench/* | head -1)"
mx2/bin/mx validate "$RUN_DIR"
mx2/bin/mx submit "$RUN_DIR"
```

```bash
mx2/bin/mx plan-kernel \
  --uarch gainestown \
  --tech mram14 \
  --cores 4 \
  --l3 32 \
  --blis-sizes 512 \
  --roi-m 50 --warmup-m 0 \
  --variant-set baseline \
  --tag kern_smoke

RUN_DIR="$(ls -dt results_test/kernel/* | head -1)"
mx2/bin/mx validate "$RUN_DIR"
mx2/bin/mx submit "$RUN_DIR"
```

```bash
mx2/bin/mx plan-hca \
  --uarch gainestown \
  --tech mram14 \
  --benches 505.mcf_r \
  --l3 32 \
  --cores 1 \
  --roi-m 50 --warmup-m 10 \
  --variants baseline_sram_only,baseline_mram_only \
  --tag hca_smoke_base

RUN_DIR="$(ls -dt results_test/hca/*hca_smoke_base | head -1)"
mx2/bin/mx validate "$RUN_DIR"
mx2/bin/mx submit "$RUN_DIR" --dry-run
mx2/bin/mx submit "$RUN_DIR"
mx2/bin/mx verify "$RUN_DIR"
```

```bash
mx2/bin/mx plan-hca \
  --uarch gainestown \
  --tech mram14 \
  --benches 505.mcf_r \
  --l3 2,32,128 \
  --cores 1 \
  --roi-m 50 --warmup-m 10 \
  --variants baseline_mram_only,grid_s8_fillsram,mig_s8_fillsram_p8_c32 \
  --tag hca_smoke_hybrid

RUN_DIR="$(ls -dt results_test/hca/*hca_smoke_hybrid | head -1)"
mx2/bin/mx validate "$RUN_DIR"
mx2/bin/mx submit "$RUN_DIR"
mx2/bin/mx verify "$RUN_DIR"
```

```bash
mx2/bin/mx plan-hca \
  --uarch gainestown \
  --tech mram32 \
  --benches 505.mcf_r \
  --l3 32 \
  --cores 1 \
  --roi-m 50 --warmup-m 10 \
  --variants baseline_sram_only,baseline_mram_only \
  --tag hca_mram32_smoke

RUN_DIR="$(ls -dt results_test/hca/*hca_mram32_smoke | head -1)"
mx2/bin/mx validate "$RUN_DIR"
mx2/bin/mx submit "$RUN_DIR"

mx2/bin/mx plan-hca --uarch gainestown --tech sram7  --benches 505.mcf_r --l3 32 --cores 1 --roi-m 50 --warmup-m 10 --variants baseline_sram_only --tag hca_sram7_smoke
mx2/bin/mx plan-hca --uarch gainestown --tech sram32 --benches 505.mcf_r --l3 32 --cores 1 --roi-m 50 --warmup-m 10 --variants baseline_sram_only --tag hca_sram32_smoke

```


---

## VF Sensitivity Study (Discussion section — piecewise-linear power model)

### Background

The main LeakDVFS model uses a linear dynamic power estimator:
```
P_est = p_static_w + llc_leak_w + k_dyn * Σ(f_c * u_c)
```

For the paper's Discussion section we implemented a frequency-indexed piecewise-linear
model (PLM) that is more realistic without requiring a full V/f hardware characterization:
```
P_est     = llc_leak_w + P_nocache_est(f)
P_nocache = b_f  +  a_util * U_sum  +  a_ipc * U_sum * ipc_interval
```

One set of coefficients `(b_f, a_util, a_ipc)` per DVFS operating frequency.
The current DVFS frequency is used as the exact lookup key; nearest fallback if no
exact entry exists (logged with a warning, once per distinct miss frequency).

### Predictors

| Predictor              | Definition                                                          | Units      |
|------------------------|---------------------------------------------------------------------|------------|
| `U_sum`                | Total utilization summed across all cores: `avg_util × N_cores`    | core-units |
| `U_sum × ipc_interval` | Interaction term: busy-core compute intensity, scales with N        | core-units × IPC |

`U_sum` (not `avg_util`) is used so the model is **portable across core counts**: at the
same per-core workload intensity, doubling N doubles U_sum and doubles predicted dynamic
power, matching physical expectation. A model fitted on n=1 or n=8 data can be applied
to n=4 deployments (subject to portability validation below).

`ipc_interval` is **not** per-core hardware PMU IPC. It is the ratio of total
instructions retired across all cores to estimated total elapsed cycles (using
mean core frequency as cycle-rate proxy). Values > 1 are possible with superscalar
issue > 1.

### Calibration

Coefficients are fitted offline via OLS linear regression at each fixed DVFS frequency.
The calibration target is McPAT non-cache package power:
```
P_nocache_McPAT  =  P_total_McPAT − P_LLC_leak
                 =  b_f  +  a_util * U_sum  +  a_ipc * U_sum * ipc_interval
```

All three coefficients `(b_f, a_util, a_ipc)` come from a single consistent regression
procedure — no literature-derived constants are mixed in.

**Calibration data for sunnycove:**

| Dataset | N | Frequencies | Runs | Source |
|---------|---|-------------|------|--------|
| Primary (existing) | 8 | 19 frequencies (2.0–3.6 GHz) | 570 | `results_test/calibration/sunnycove_spec10_l3_sram14_roi1000_warm200_SRAMONLY/spec/` |
| New (to run) | 1 | 2.0, 2.66, 3.2 GHz | 18 | `plm_calibrate_sweep.sh --mode calib` |

Note: the existing data uses single-threaded SPEC workloads on n=8 cores, so U_sum ≤ 1
in both datasets. The n=1 calibration jobs add coverage at a different static power
operating point (1-core P_static vs 8-core), which helps separate b_f from a_util.

The runner ships with **linear-model-equivalent placeholder coefficients** until
offline calibration is run:
```
b_f    = p_static_w          (20.08 W for sunnycove)
a_util = k_dyn * f_ghz / N  (scales k_dyn to W/U_sum units)
a_ipc  = 0.0                 (zero until regression is run)
```
With these placeholders, the PLM exactly reproduces the original linear model,
making it straightforward to verify correctness before substituting real coefficients.

#### Step 1 — Run n=1 sunnycove calibration jobs

```bash
cd ~/COSC_498/miniMXE/mx2

# Generate 18 jobs (6 benches × 3 freqs, n=1, fixed freq, SRAM-only, LC off)
bash plm_calibrate_sweep.sh --mode calib
mx submit <N1_RUN_DIR>
mx verify  <N1_RUN_DIR>

# Extract oracle points
SNIPER_HOME=~/src/sniper ROOT=<N1_RUN_DIR>/runs \
    bash tools/extract_oracle_points.sh
```

#### Step 2 — Fit PLM from combined n=1 + n=8 data

```bash
python3 tools/mcpat_plm_fit.py \
    --csv ~/COSC_498/miniMXE/results_test/calibration/sunnycove_spec10_l3_sram14_roi1000_warm200_SRAMONLY/spec/oracle_points_plus.csv \
    --extra-csv <N1_RUN_DIR>/runs/oracle_points.csv \
    --sniper-home ~/src/sniper \
    --uarch sunnycove --calib-ncores 8 \
    --out plm_sunnycove_cal.sh
```

`mcpat_plm_fit.py` outputs per-frequency R², MAE, and condition number, and writes
`plm_sunnycove_cal.sh` with PLM_N/PLM_F/PLM_B/PLM_AUTIL/PLM_AIPC arrays.

#### Step 3 — Portability validation (n=4)

```bash
# Generate 12 validation jobs (6 benches × 2 freqs, n=4)
bash plm_calibrate_sweep.sh --mode validate
mx submit <VAL_RUN_DIR>
mx verify  <VAL_RUN_DIR>

SNIPER_HOME=~/src/sniper ROOT=<VAL_RUN_DIR>/runs \
    bash tools/extract_oracle_points.sh

python3 tools/mcpat_plm_fit.py \
    --csv ~/COSC_498/miniMXE/results_test/calibration/sunnycove_spec10_l3_sram14_roi1000_warm200_SRAMONLY/spec/oracle_points_plus.csv \
    --extra-csv <N1_RUN_DIR>/runs/oracle_points.csv \
    --sniper-home ~/src/sniper \
    --uarch sunnycove --calib-ncores 8 \
    --validate-csv <VAL_RUN_DIR>/runs/oracle_points.csv \
    --validate-ncores 4 \
    --out plm_sunnycove_cal.sh
```

**Interpreting validation output:**

| Condition | Diagnosis | Action |
|-----------|-----------|--------|
| bias < 1 W, corr(resid, U_sum) < 0.5, corr(resid, U_sum×ipc) < 0.5 | PASS — model portable | Use fitted coefficients as-is |
| nonzero bias, low correlation | Intercept offset only | Apply scalar b_f correction |
| corr(resid, U_sum) ≥ 0.5 | a_util slope error | Run separate n=4 calibration |
| corr(resid, U_sum×ipc) ≥ 0.5 | a_ipc slope error | Run separate n=4 calibration |

### Sniper changes

Two files modified in `common/system/`:
- `leakage_conversion.h` — added `FreqModelEntry` struct (`f_ghz`, `intercept`, `a_util`, `a_ipc`),
  `m_plm_enabled`, `m_plm_verbose`, `m_freq_models`, `m_last_global_ins` members,
  plus `selectModel()` / `evaluatePLM()` private helpers
- `leakage_conversion.cc` — reads `lc/piecewise/*` knobs; in `onPeriodicIns` computes
  `ipc_interval` and `U_sum = avg_util × N_cores`; PLM branch does exact-then-nearest
  frequency lookup; verbose per-interval log; DVFS-change log extended with `P_nocache`,
  `f_lookup`, match type, `u_sum`, `ipc`, `u_sum_x_ipc`

New Sniper config knobs (all optional, default off):
```
lc/piecewise/enabled      = false    # set true to activate PLM
lc/piecewise/verbose      = false    # if true, log every control interval
lc/piecewise/n_models     = N        # number of frequency table entries

# For each i in [0, N-1]:
lc/piecewise/i/f_ghz      = <f>      # DVFS frequency this entry applies to (GHz)
lc/piecewise/i/b          = <b>      # intercept b_f (W)
lc/piecewise/i/a_util     = <au>     # U_sum coefficient (W / core-unit)
lc/piecewise/i/a_ipc      = <ai>     # U_sum×ipc coefficient (W / (core-unit × IPC))
```

Existing behavior is 100% unchanged when `lc/piecewise/enabled` is absent or false.

Extensibility: to add a new predictor (e.g. MPKI), add `a_mpki` to `FreqModelEntry`,
measure it in `onPeriodicIns`, add the term in `evaluatePLM()`, and add the config key.

### mx2 changes

- `runner/dispatch.sh` — routes `CAMPAIGN=vf_sensitivity` to `run_vf_sensitivity.sh`;
  routes `CAMPAIGN=plm_calib` to `run_plm_calib.sh`
- `engine/run_vf_sensitivity.sh` — runner (based on `run_traces.sh`); appends `lc/piecewise/*`
  flags to `VAR_FLAGS` after standard variant flags; embeds 7-point placeholder table;
  supports `PLM_CFG_SH` env var override for calibrated coefficient files
- `engine/run_plm_calib.sh` — fixed-frequency calibration/validation runner; single bench,
  fixed `BASE_FREQ_GHZ`, configurable `SIM_N`, SRAM-only, LC disabled; writes `run.yaml`
  and `cmd.info` compatible with `extract_oracle_points.sh`
- `vf_sensitivity_study.sh` — standalone sweep script; generates run directory directly;
  passes `PLM_CFG_SH` through to jobs if set
- `plm_calibrate_sweep.sh` — sunnycove calibration and validation sweep; `--mode calib`
  generates 18 n=1 calibration jobs (6 benches × 3 freqs); `--mode validate` generates
  12 n=4 portability validation jobs (6 benches × 2 freqs); prints full next-step workflow
- `tools/mcpat_plm_fit.py` — PLM fitting and validation tool:
  - Fits per-frequency OLS with predictors `[1, U_sum, U_sum×ipc]`
  - Runs McPAT automatically if `mcpat_table.txt` is missing
  - `--validate-csv` mode: reports per-frequency bias, MAE, MAPE; residual decomposition
    via Pearson correlation with U_sum and U_sum×ipc; PASS/WARN verdict with diagnostics
  - Writes `plm_<uarch>_cal.sh` coefficient file for use with `run_vf_sensitivity.sh`

### Running the study

```bash
# 1. Fit PLM coefficients (see Calibration → Step 1 above)
python3 mx2/tools/mcpat_plm_fit.py --csv <calib.csv> ... --out plm_sunnycove_cal.sh

# 2. Plan vf_sensitivity jobs
PLM_CFG_SH=~/COSC_498/miniMXE/mx2/plm_sunnycove_cal.sh \
  bash ~/COSC_498/miniMXE/mx2/vf_sensitivity_study.sh

# 3. Submit
~/COSC_498/miniMXE/mx2/bin/mx submit \
  ~/COSC_498/miniMXE/results_test/vf_sensitivity/vf_sensitivity

# 4. Check status
~/COSC_498/miniMXE/mx2/bin/mx verify \
  ~/COSC_498/miniMXE/results_test/vf_sensitivity/vf_sensitivity
```

### Study scope

- Uarch: sunnycove, LLC: 32MB, Cores: n=4
- 5 representative n=4 workload mixes
- 6 variants: `baseline_sram_only` (sram7 + sram14), `baseline_mram_only` (mram14),
  `lc_*`, `naive_lc_*`, `sram_lc_*` (all mram14 for LC variants)
- Total: 30 jobs
- Results land in: `results_test/vf_sensitivity/vf_sensitivity/`

### Interpreting results

The `run.yaml` for each vf_sensitivity run includes:
```yaml
power_model: piecewise_linear
plm_n_models: 7
plm_verbose: false
plm_cfg_sh: <builtin defaults or path to cal file>
```

The Sniper log (`sniper.log`) will show at init:
```
[LC] Initialized: ... power_model=piecewise_linear n_models=7
[LC] PLM[0]: f=1.60 GHz  b=20.0800  a_util=3.9200  a_ipc=0.0000
...
[LC] PLM[6]: f=4.00 GHz  b=20.0800  a_util=9.8000  a_ipc=0.0000
```

And at each DVFS transition:
```
[LC] DVFS Change [PLM]: P_est=X (llc_leak=Y P_nocache=Z) Target=T
     f_lookup=F(exact) u_sum=U ipc=I u_sum_x_ipc=V boosted=k/N f[min/avg/max]=[...]
```

And (if `lc/piecewise/verbose=true`) at every control interval:
```
[LC-PLM] f_lookup=F match=exact u_sum=U ipc=I u_sum_x_ipc=V P_nocache=Z P_llc_leak=Y P_est=X
```

---

## Submitting with different SLURM resources

`array_runner.sbatch` has defaults, but you can override at submit time:

Example:
```bash
mx2/bin/mx submit <run_dir> \
  --sbatch=--time=72:00:00 \
  --sbatch=--mem=32G \
  --sbatch=--partition=cpu-dense-preempt-q
```

Full sweep:

```bash
# -----------------------
# 1) SPEC full sweep
# -----------------------
mx2/bin/mx plan-spec \
  --uarch gainestown \
  --tech mram14 \
  --cores 4 \
  --l3 2,32,128 \
  --roi-m 1000 --warmup-m 200 \
  --variant-set all \
  --tag full

SPEC_RUN="$(ls -dt results_test/spec/*full | head -1)"
mx2/bin/mx validate "$SPEC_RUN"
mx2/bin/mx submit "$SPEC_RUN" --sbatch=--time=72:00:00 --sbatch=--mem=8G


# -----------------------
# 2) TRACES global full sweep
# -----------------------
mx2/bin/mx plan-traces \
  --uarch gainestown \
  --tech mram14 \
  --cores 4 \
  --l3 2,32,128 \
  --roi-m 1000 --warmup-m 200 \
  --variant-set all \
  --tag global

TR_RUN="$(ls -dt results_test/traces/*global | head -1)"
mx2/bin/mx validate "$TR_RUN"
mx2/bin/mx submit "$TR_RUN" --sbatch=--time=72:00:00 --sbatch=--mem=32G


# -----------------------
# 3) TRACES selective LeakDVFS sweep (top-2 cores)
# (LeakDVFS only; no baselines here)
# -----------------------
mx2/bin/mx plan-traces \
  --uarch gainestown \
  --tech mram14 \
  --cores 4 \
  --l3 2,32,128 \
  --roi-m 1000 --warmup-m 200 \
  --variant-set leakdvfs \
  --dvfs selective --topk 2 \
  --tag sel_k2

TR_SEL="$(ls -dt results_test/traces/*sel_k2 | head -1)"
mx2/bin/mx validate "$TR_SEL"
mx2/bin/mx submit "$TR_SEL" --sbatch=--time=72:00:00 --sbatch=--mem=32G


# -----------------------
# 4) Microbench full sweep
# -----------------------
mx2/bin/mx plan-microbench \
  --uarch gainestown \
  --tech mram14 \
  --cores 4 \
  --l3 2,32,128 \
  --roi-m 1000 --warmup-m 200 \
  --variant-set all \
  --tag full

MB_RUN="$(ls -dt results_test/microbench/*full | head -1)"
mx2/bin/mx validate "$MB_RUN"
mx2/bin/mx submit "$MB_RUN" --sbatch=--time=24:00:00 --sbatch=--mem=8G


# -----------------------
# 5) Kernel full sweep (BLIS sizes default)
# -----------------------
mx2/bin/mx plan-kernel \
  --uarch gainestown \
  --tech mram14 \
  --cores 4 \
  --l3 2,32,128 \
  --roi-m 1000 --warmup-m 200 \
  --variant-set all \
  --tag full

K_RUN="$(ls -dt results_test/kernel/*full | head -1)"
mx2/bin/mx validate "$K_RUN"
mx2/bin/mx submit "$K_RUN" --sbatch=--time=24:00:00 --sbatch=--mem=8G

# -----------------------
# 6) HCA full sweep
# -----------------------

# mram32 full HCA sweep (12 benches × 3 L3 × 18 variants = 648 jobs)
mx2/bin/mx plan-hca \
  --uarch gainestown \
  --tech mram32 \
  --l3 2,32,128 \
  --cores 4 \
  --roi-m 1000 --warmup-m 200 \
  --tag hca_mram32_full

RUN_DIR="$(ls -dt results_test/hca/*hca_mram32_full | head -1)"
mx2/bin/mx validate "$RUN_DIR"
mx2/bin/mx submit "$RUN_DIR" --sbatch=--time=72:00:00 --sbatch=--mem=8G

# sram7 baseline-only (12 benches × 3 L3 × 1 variant = 36 jobs)
mx2/bin/mx plan-hca \
  --uarch gainestown \
  --tech sram7 \
  --l3 2,32,128 \
  --cores 4 \
  --roi-m 1000 --warmup-m 200 \
  --variants baseline_sram_only \
  --tag hca_sram7_base

RUN_DIR="$(ls -dt results_test/hca/*hca_sram7_base | head -1)"
mx2/bin/mx validate "$RUN_DIR"
mx2/bin/mx submit "$RUN_DIR" --sbatch=--time=72:00:00 --sbatch=--mem=8G

# sram32 baseline-only (12 benches × 3 L3 × 1 variant = 36 jobs)
mx2/bin/mx plan-hca \
  --uarch gainestown \
  --tech sram32 \
  --l3 2,32,128 \
  --cores 4 \
  --roi-m 1000 --warmup-m 200 \
  --variants baseline_sram_only \
  --tag hca_sram32_base

RUN_DIR="$(ls -dt results_test/hca/*hca_sram32_base | head -1)"
mx2/bin/mx validate "$RUN_DIR"
mx2/bin/mx submit "$RUN_DIR" --sbatch=--time=72:00:00 --sbatch=--mem=8G

# mram14 full HCA sweep (also 648 jobs)
mx2/bin/mx plan-hca \
  --uarch gainestown \
  --tech mram14 \
  --l3 2,32,128 \
  --cores 4 \
  --roi-m 1000 --warmup-m 200 \
  --tag hca_mram14_full

RUN_DIR="$(ls -dt results_test/hca/*hca_mram14_full | head -1)"
mx2/bin/mx validate "$RUN_DIR"
mx2/bin/mx submit "$RUN_DIR" --sbatch=--time=72:00:00 --sbatch=--mem=8G

```

Helpful runs:

```bash
# LeakDVFS tuning runs
UARCH="gainestown"
TECH="mram14"
BENCHES="505.mcf_r"
L3_LIST="2,32,128"
CORES="1"
ROI="1000"
WARM="200"

HYST_LIST="0.10,0.20,0.35"
PI_LIST="500000,1000000,2000000"
STEP_LIST="0.05,0.10,0.15"

for H in ${HYST_LIST//,/ }; do
  for PI in ${PI_LIST//,/ }; do
    for ST in ${STEP_LIST//,/ }; do
      mx2/bin/mx plan-spec \
        --uarch "$UARCH" --tech "$TECH" \
        --benches "$BENCHES" --l3 "$L3_LIST" --cores "$CORES" \
        --roi-m "$ROI" --warmup-m "$WARM" \
        --variant-set leakdvfs \
        --hyst-w "$H" \
        --ldvfs-periodic-ins "$PI" \
        --step-ghz "$ST" \
        --tag "tune_l3${L3_LIST}_roi${ROI}_warm${WARM}_h${H}_pi${PI}_st${ST}"
    done
  done
done
```

```bash
# Bash
UARCH="gainestown"
TECH="sram14"
BENCHES="500.perlbench_r"
L3_LIST="2,32,128"
CORES="1"
ROI="1000"
WARM="200"

OUTROOT="results_test/calibration/${UARCH}_perlbench_l3_${TECH}_roi${ROI}_warm${WARM}_leakdvfs3"

for F in 2.0 2.66 3.2; do
  mx2/bin/mx plan-spec \
    --out "$OUTROOT" \
    --uarch "$UARCH" \
    --tech "$TECH" \
    --benches "$BENCHES" \
    --l3 "$L3_LIST" \
    --cores "$CORES" \
    --roi-m "$ROI" --warmup-m "$WARM" \
    --variant-set leakdvfs3 \
    --base-freq-ghz "$F" \
    --tag "f${F}"
done

for RUN_DIR in "$OUTROOT"/spec/*_spec_gainestown_f*; do
  mx2/bin/mx validate "$RUN_DIR"
  mx2/bin/mx submit "$RUN_DIR"
done
```
