# SunnyCove HCA Study – Walkthrough

## What Was Done

### 1. Device YAML Updates

Added 16MB NVSim entries (from `nvsim_optimal.txt`) to three device files that were missing them:

| File | rd_cyc | wr_cyc | r_pj | w_pj | leak_mw |
|---|---:|---:|---:|---:|---:|
| `sram7.yaml` | 8 | 5 | 313 | 296 | 193.0 |
| `sram32.yaml` | 19 | 10 | 1347 | 1249 | 99.4 |
| `mram32.yaml` | 15 | 34 | 1151 | 814 | 53.8 |

### 2. Master Script

Created `sunnycove_hca_study.sh` — plans all 4 sub-studies via `mx plan-hca`:

| Study | Dir | Configs | Jobs |
|---|---|---|---:|
| 1. Cross-Node | `1_cross_node/{sram7,sram14,sram32,mram14,mram32}` | 5 tech baselines | 150 |
| 2. Static Policy | `2_static_policy` | 3 grid + 3 noparity @ s{4,8,12} | 180 |
| 3. Migration Sweep | `3_migration_sweep` | 48 noparity mig (3×4×4) | 1,440 |
| 4. Latency Sweep | `4_latency_sweep/lat_{2,3,4,5}x` | 4 MRAM latency scales | 120 |
| **Total** |  |  | **1,890** |

> **NOTE**  
> Study 3 yields 1,440 jobs (48 configs × 30 patterns), not 720 as originally estimated. SRAM16 ≡ `sram14`, MRAM16 ≡ `mram14`.

### 3. Directory Layout

```plaintext
results_test/hca/sunnycove_hca/
├── 1_cross_node/
│   ├── sram7/    (30 jobs)
│   ├── sram14/   (30 jobs)
│   ├── sram32/   (30 jobs)
│   ├── mram14/   (30 jobs)
│   └── mram32/   (30 jobs)
├── 2_static_policy/   (180 jobs)
├── 3_migration_sweep/ (1440 jobs)
└── 4_latency_sweep/
    ├── lat_2x/   (30 jobs)
    ├── lat_3x/   (30 jobs)
    ├── lat_4x/   (30 jobs)
    └── lat_5x/   (30 jobs)