# Digital contribution batching census (2026-11-14)

This measures the stage-zero gate in `docs/REDPILATOR_OPTIMIZATION_PLAN.md`.
No runtime rewrite is enabled by this change.

**Decision: no-go for the first proposed rule.** All seven CPU families have zero
candidate runs in both modes. Per the plan's stage-zero stop condition, do not add
descriptors, a checker or Rust/C batch kernels for these tables. Revisit the rule
or lowering strategy before proceeding to implementation stages 2–7.

| Fixture | Strict ranges / flips | Relaxed ranges / flips | Candidate runs (both modes) |
| --- | ---: | ---: | ---: |
| ANPU Pong | 227 / 530 | 1,963 / 4,206 | 0 |
| BubbleSort | 12,419 / 43,678 | 29,017 / 99,086 | 0 |
| ZCore12B | 13,350 / 46,670 | 30,701 / 103,112 | 0 |
| PM1 current protocol fixture | 62,659 / 205,658 | 120,359 / 373,536 | 0 |
| PC_COUNTER | 455 / 1,960 | 841 / 3,490 | 0 |
| FPU_27 | 6,103 / 19,436 | 11,363 / 49,972 | 0 |
| CHUNGUS | 0 / 0 | 0 / 0 | 0 |

The separate legacy `instant-pistons/PM1_SORT.schem` rejects compilation in both
modes at `(206, 42, 40)` with `moving piston state is unsupported`; it is not
counted as a measured zero. Machine-readable counts, compile durations and
rejections are in `digital-batch-census.json`. No fixture stimuli were executed
for the static census; these are whole-program final-table counts.

The diagnostic scans each unique final canonical fixed range after relaxed-mode
transformation and movement folding. It counts maximal consecutive main-channel
token runs with one target and distinct link IDs. It intentionally omits the
target-kind whitelist, immutable-value verification and other checker conditions:
this is an upper bound on eligible batches, not a verified descriptor generator.
A zero upper bound implies zero possible dynamic batch executions in either
canonical backend, regardless of inputs or guard success. Native graph replay is
outside the proposed rule. No dynamic tick instrumentation is necessary when the
static upper bound is zero. Estimated retained plan bytes are then zero.

Reproduce from PowerShell at the repository root:

```powershell
$env:MCHPRS_REDPILATOR_PHASE_TIMINGS='1'
cargo run --release -p mchprs_core --example census_sampled --locked
Remove-Item Env:MCHPRS_REDPILATOR_PHASE_TIMINGS
cargo test -p mchprs_core --lib redpilator --locked
cargo check -p mchprs_core --all-targets --all-features --locked
cargo run --release -p mchprs_core --example bench_cpus --locked -- zcore 5000 5 kernel
```

Source base: `4ef372fe2da16107cb08573944520c32e943c0f3` plus the accompanying
census changes. Machine: Windows, AMD Ryzen 9 5950X, 16 cores / 32 threads.
Rust: `rustc 1.98.1 (48a229cea 2026-09-01)`;
Cargo: `1.98.1 (797e8a9bc 2026-08-05)`.
Default release profile, fat LTO; no custom flags or affinity.
Schematic hashes are in `digital-batch-fixture-hashes.json`.
CHUNGUS uses `crates/core/benches/chungus_mandelbrot_plot` and its existing loader.
Its SHA-256 is `31624f7aa8e050f065f6858ba54007fe75c6f33e333138ee77d8dd6cc5291d78`.

The ZCore benchmark is a baseline reference only. It includes interpreter output
generation and checks final reset state outside the kernel timing window. It does
not establish per-tick equivalence or any optimization speedup. Census, tests and
builds overlapped some samples, so timings are exploratory and cannot satisfy the
plan's controlled paired performance gate.

Validation: focused census test passes; all-target/all-feature check passes.
The full Redpilator filter reports 279 passed, 54 failed, 30 ignored. Many failures
report LLVM module preparation errors; `clang` is unavailable on PATH in this
environment. An isolated pressure-plate test reproduces that preparation error.
The full failure log is retained locally under the ignored work directory.
The plot test `executed_plot_reuses_exported_state_and_keeps_pending_ticks_once`
also reproduces an `Option::unwrap()` failure in isolation; the suite is not green.

Five strict Rust ZCore samples of 5,000 ticks each returned `final_reset_match`.
Median kernel throughput was **1,598.53 ticks/s** (range 1,455.86–1,659.42).
Median compilation duration was 19.18 seconds. Individual measurements are in
`digital-batch-baseline.json`; full benchmark output is under
`target/redpilator-bench-results/zcore-kernel.json`.
