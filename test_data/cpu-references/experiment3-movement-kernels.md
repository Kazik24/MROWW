# Experiment 3: profile-guided native movement kernels

**Decision: do not adopt the bounded dispatcher in production.** It works on the
tested traces, but its incremental gain over a much smaller alignment control
is below the 3% target. Keep the experiment and measurements. This does not
reject all forms of movement specialization.

ZCore12B, Windows / Ryzen 9 5950X, affinity mask 256, 413 preparation ticks,
redstone publication setting with no publication/client work inside timing.
Each row below has five alternating A/B pairs, reversing order between pairs.
No builds, tests or census overlapped paired timing. The selected kernels were
frozen after the initial 5,000-tick profiles; longer tests did not retrain them.

| Comparison | Timed ticks | Median paired throughput change |
| --- | ---: | ---: |
| Relaxed: generated vs original generic | 5,000 | **−3.18%** |
| Strict: generated vs original generic, initial noisy run | 5,000 | +3.03% |
| Strict: generated vs original generic, longer repeat | 50,000 | +5.81% |
| Strict: generated vs unused generated-body control | 50,000 | +4.51% |
| Strict: alignment-only vs original generic | 50,000 | +4.13% |
| Strict: generated vs alignment-only, direct comparison | 50,000 | **+1.46%** |

All 60 timed samples passed their unchanged frozen output/reset checks. Every
pair matched the complete existing benchmark snapshot hash (including ordered
pending work, motion, contributions, input counts, notes/command effects and
logical work). These are exported semantic snapshots, not raw memory equality.

## Census and implementation

The profiler records actual graph physical ranges separately from canonical
fixed ranges, only at existing guarded replay points. It counts scalar fallback
flips without specializing them. The first 16,384-slot profile overflowed and
was discarded. Final profiles use 131,072 slots (4 MiB of global counters), with
zero overflow. Counters and serialized TSC reads exist only in profiling DLLs;
timed DLLs contain neither. TSC samples rank ranges, not unperturbed CPU-time
attribution; instrumentation affects timing and compiler decisions.

| 5,000-tick profile | Strict | Relaxed |
| --- | ---: | ---: |
| Distinct nonempty guarded ranges | 5,538 | 24,730 |
| Nonempty guarded calls | 887,717 | 3,425,234 |
| Guarded physical flips | 4,468,488 | 8,805,320 |
| Physically empty guarded calls | 430,428 | 4,596,800 |
| Scalar fallback flips | 9,150,111 | 0 |
| Selected physical-flip coverage | 3.81% | 1.29% |

Scalar fallback is often state-dependent replay, not an observed operation-limit
failure. Empty physical ranges offer no flips to specialize. Grouping identical
relaxed record sequences barely improves the top-16 profile concentration.
The extended strict profile also has zero overflow: 46,322,331 guarded flips,
93,706,546 scalar fallback flips, and 1,707,487 flips in the frozen selected set.

The offline tool copies a circuit module emitted from validated final tables.
Each generated function calls the unchanged `apply_movement_flip` primitive
with the original four-word records in original order. No batching, source-value
assumption, callback removal or state-space pruning is involved. A bounded hash
dispatch checks path, start and length exactly; unknown ranges use generic
replay. Graph dispatch uses its actual filtered physical records, preserving
direct-range/prefix selection and materialization. No graph path is disabled.
Budget checks, group advancement and original logical charges stay at their
original call sites. Failed guards never enter generated replay.

Limits: 16 kernels, 32 flips per range, 256 flips total and at most 32 KiB of
additional PE `.text`, checked after linking. Strict emitted 255 flips and grew
code by 9,119 bytes; relaxed emitted 169 and grew code by 19,814 bytes. No new
per-instance simulation buffers are allocated. Native C compile/link durations
and image sizes are recorded in the JSON; full compiler peak memory was not
collected. Rust circuit compilation and native C compilation are separate costs.

## Assembly and correctness

Generated assembly has immediate component/link IDs and constant offsets;
some output/contribution helper calls remain. Inspection also found that the
generic flip helper moved from address modulo 64 = 48 to 0. An alignment-only
copy, with no generated kernels, reproduces much of the original gain with just
16 additional code bytes. It is a control, not an adopted production change.
The unused-body control is not perfectly layout-matched; the alignment control
and direct comparison prevent attributing the whole +5.81% to operand decoding.

The strict generated DLL passed the existing named ignored
`llvm_zcore_matches_rust_state_and_motion_each_tick` check with 5,000 run ticks
plus preparation, comparing against the independent Rust executor every tick.
Nine focused movement tests and four Python generator self-checks pass.

A separate diagnostic pair injects remaining budgets 0, 1, 2, 3 and 4 before an
actual selected three-flip range (canonical start 2,141,986). All snapshot hashes,
errors, injection positions and logical counters match generic native replay.
An observer confirms zero generated calls for budgets 0–2 and one for 3–4.
This covers successful scalar prefixes, exact full-range admission and one
operation beyond it. The hook is absent from timed and production modules.

All-target/all-feature checks pass. Production native runtime/generator source
hashes match the prior 335-pass serial baseline; the full suite was not repeated.
No frozen expectation changed. Production generation, format, cache identity
and simulation ABI remain unchanged; explicit private DLL overrides isolate the
experiment. Only benchmark-only Rust accessors and experiment tooling remain.

## Reproduction and evidence

Source base: `7347a517f9af04e2dad8037f870460de7cd7fa2d` plus the accompanying
benchmark hooks/tools. `experiment3-movement-kernels.json` records all paired
samples, artifacts, selections, tools, hashes, costs, guard results and limits.
The three `experiment3-profile-*.json` files are the saved raw training/coverage
measurements. Reuse saved training profiles to reproduce the same selection.
Each raw file has four header words (overflow, empty graph ranges, empty
canonical ranges, scalar fallback flips), then five-word rows (path: 1 graph /
2 canonical, start, count, hits, TSC ticks). Instrumented TSC ticks are not
retired CPU cycles or a bound on uninstrumented whole-kernel gains.
All pairs used the same runner binary, whose hash is recorded. The final
benchmark source simplifies an extra counter-reset query outside timing;
simulation source and timed native code are unchanged.

From PowerShell at the repo root (use fresh output directories):

```powershell
. ./test_data/cpu-references/redpilator-next-env.ps1 kernel-seed
cargo build --release -p mchprs_core --features plot-bench --example bench_engine_compare --example experiment_movement_kernel_guards --locked
New-Item -ItemType Directory -Force work/kernel-repro | Out-Null
$env:MCHPRS_ENGINE_KERNEL_ANALYSIS='1'
(Get-Process -Id $PID).ProcessorAffinity=256
target/release/examples/bench_engine_compare.exe zcore work/kernel-repro/seed.json 5000 --samples=1
$seed=(Get-ChildItem "$env:MCHPRS_LLVM_WORK_DIR/mroww-native-cache" -Directory | Select-Object -First 1).FullName
py -3 tools/experiment_movement_kernels.py baseline $seed work/kernel-repro/baseline
py -3 tools/experiment_movement_kernels.py specialize $seed work/kernel-repro/generated test_data/cpu-references/experiment3-profile-strict.json
py -3 tools/experiment_movement_kernels.py align $seed work/kernel-repro/align
# Once all builds/tests finish, run one DLL at a time; reverse A/B order across five pairs:
$env:MCHPRS_LLVM_DLL=(Resolve-Path work/kernel-repro/generated/engine.dll).Path
target/release/examples/bench_engine_compare.exe zcore work/kernel-repro/generated-run.json 50000 --samples=1
```

`profile` builds instrumentation; `layout` retains unused bodies; `guard-baseline`
and `guard-specialize` create prefix-test modules for the guard example.
`py -3 tools/experiment_movement_kernels.py selftest` checks extraction/order,
profile bounds, code-size parsing and source-anchor drift. Raw C, disassembly,
DLLs, snapshots and logs stay under ignored `work/movement-kernels/`.

Other six CPU protocols, live client delivery and broader alignment checks were
not run. Relaxed adoption fails the regression gate; strict codegen does not
justify its complexity over the alignment control in this bounded experiment.
No pure electrical-region or transition-machine experiment was started.
