# Experiment 4: offline helper alignment

**Decision: blocked at timing calibration. No production alignment change.**
The functional baseline works, but separate images with identical code and data
produce apparent improvements comparable to the 3% gain / 2% regression gates.
Ranking those artifacts would select an unresolved measurement effect along
with any alignment effect. This does not establish that alignment cannot help.

## Baseline and scope

Base revision `7347a517f9af04e2dad8037f870460de7cd7fa2d`, plus the preserved
pre-existing kernel experiment and the accompanying benchmark tooling.
`experiment4-source-diff.patch` records the tracked code diff as measured;
`experiment4-alignment-provenance.json` records file, fixture, tool and runner
hashes and the initial dirty paths. No commit, reset or unrelated cleanup ran.
Production `engine.c`, `llvm_movement.c`, generated tables and compiler flags
remain unchanged.

Windows x64, Ryzen 9 5950X, logical processor 8 / affinity mask 256, Balanced
power scheme. Explicit Unity Clang 22.0.0git and Rust LLD 22.1.8 were used with
fresh process-owned LLVM work roots and no inherited DLL override. The actual
benchmark process affinity was verified. No other experiment builds, tests,
profiles or census ran during controlled timing. Unrelated desktop services
were not stopped; CPU frequency was not locked. These results describe this
system only.

The initial serial baseline passed **335 tests, 0 failed, 30 ignored**. The
final serial suite with `plot-bench` also passed **335 / 0 / 30** (443.20 s).
The final all-target/all-feature core check passed. The explicitly selected
ignored ZCore/Rust state-and-motion differential passed every tick through
413 preparation + 5,000 run ticks (133.01 s). No ignored capture/export tool
was run. Exact commands and results are recorded in the JSON.
The known parallel cold-build activation issue remains separate and unresolved.

PM1 means `pm1_redpilator.schem` and its saved Start-only, zero-preparation,
12,070-tick protocol/reference. It is neither legacy `PM1_SORT.schem` nor
`PM1_SORT_FIXED.schem`. Legacy compilation rejection is not a measured zero.

## Bounded implementation and layout census

`tools/experiment_alignment.py` reuses the movement experiment's source-anchor
and PE utilities. It copies original emitted modules, changes function
alignment annotations only, and retains generic execution. There is no
dispatcher, generated kernel, new runtime metadata or forced outlining.

The four parameters, in order, are `apply_movement_flip`, `set_arranged`,
`execute_output`, `drain`; values are default (`0`), 16, 32, 64, 128 bytes.
Existing valid ZCore profiles establish movement replay activity; linked
disassembly identifies these helpers and their propagation/scheduling call
chain. No new instrumented profile was used for timing or selection.

PRNG seed **2026111604** generated **24 unique configurations**, excluding the
two controls. The deterministic schedule seed is 2026111605; the implemented
single-parameter mutation seed is 2026111606. Four-leader/eight-mutation and
five-pair confirmation scheduling are implemented and self-checked, but no
real leaders, mutations or finalists were selected after calibration failed.
The 32-candidate budget was not expanded.

All **104** first-round program/configuration combinations compiled. Artifacts
are cached by source, binary tables, tools, flags, generator and configuration
identity. Every production control's linked `.text` matches its original seed.
The strict ZCore flip64 control also matches experiment 3's alignment-control
instruction hash. Link maps and linked disassembly are retained under the
owned artifact directories; hashes and addresses are in the JSON manifests.

| Training program | Production `.text` | Flip64 `.text` | Distinct `.text` images / 26 builds | Largest growth |
| --- | ---: | ---: | ---: | ---: |
| PM1 strict | 105,019 B | 105,019 B | 15 | 192 B |
| PM1 relaxed | 109,416 B | 109,416 B | 9 | 192 B |
| ZCore strict | 111,792 B | 111,808 B | 13 | 208 B |
| ZCore relaxed | 112,288 B | 112,288 B | 14 | 176 B |

All builds pass the **4 KiB** growth cap. The flip helper is already 64-byte
aligned in strict PM1, and is not emitted in either relaxed program. Its
alignment attribute has no code effect in those three workloads. The other
three helpers remain emitted. These facts were recorded rather than forcing
functions out of line. Actual requested/emitted alignments, RVAs, preferred
linked addresses, image hashes and no-effect configurations are saved.

Median native C compilation durations across each 26-build set were 7.42 s
(PM1 strict), 9.02 s (PM1 relaxed), 4.53 s (ZCore strict), 5.57 s (ZCore relaxed).
The largest observed compiler peak working set was 2.69 GiB. Individual compile
and link durations/peaks, image sizes and roughly 3.9–4.9 KB offline metadata
records are included. Some early builds overlapped baseline tests: these are
cost records, not controlled relative compile-cost claims. Complete Rust
frontend peak memory was not collected. Added production runtime metadata: 0 B.

## Calibration results

Each row below uses five alternating full-protocol pairs, exactly 12,070 ticks
per fresh executor, IO publication configured but **zero timed publication
calls**, and unchanged initialization/stimuli. Compilation, snapshot export,
frozen output checks and reset occur outside timing. Same-image A/A uses the
same production DLL for both labels. Separate-image A/B uses production and
flip64; on this PM1 program flip64 has no alignment/code effect.

| Comparison | Priority / preceding warmup executions | Median paired ratio change | Pair range |
| --- | --- | ---: | ---: |
| Same production image A/A | Normal / 0 | −0.11% | −1.26% to +1.48% |
| Separate no-effect images A/B | Normal / 3 | +2.93% | +0.93% to +6.27% |
| Separate no-effect images A/B | High / 2 per image | +1.42% | +0.76% to +9.79% |
| Fresh-session repeat of preceding row | High / 2 per image | +1.40% | −0.25% to +4.55% |

Do not pool the different priority/warmup conditions as one experiment.
All **51** calibration executions passed complete snapshot equality, unchanged
frozen lamp/RAM/screen and ordered command outputs, and quiet reset checks.
The full protocol emits the expected **1,534 ordered messages**.

The separate images are 245,259,776 bytes each and differ in **one byte at file
offset 128, inside the COFF timestamp**. All other image bytes, including code
and static program data, are identical. An untimed probe using the host's
absolute-path `LoadLibraryW` API verifies distinct handles, correct paths,
unchanged loaded instruction bytes, and actual helper alignments. It observes
different loaded image bases. It is a separate diagnostic process, not a
capture of the timed processes' addresses; it does **not** prove ASLR caused
the offset. Image mapping, allocation and host scheduling remain possible
confounders. No ASLR/compiler/loader policy was changed to obtain a result.

The screening evidence is also retained, but is not an adoption result:

- A superseded strict PM1 round completed 159 executions with matching full
  snapshots and frozen outputs. Its largest three-pair median was +12.10%,
  while its no-effect flip64 control showed +2.05%. The cohort was superseded
  before all training workloads completed, following a loader correction.
- The corrected relaxed PM1 round completed 51 executions before being stopped
  for calibration. All snapshots, frozen outputs and quiet resets matched.
  One configuration with unchanged `.text` showed +40.64% in a single short
  pair. Neither that pair nor the partial cohort is a valid improvement claim.

## Correctness fixes and coverage

Two measurement-tool failures were diagnosed rather than hidden:

1. Initially two of 3,044,022 PM1 snapshot words differed: rolling output count
   and bytes reset from wall time, even with unlimited output. The existing
   `benchmark_tick_batch(0, 0)` hook now supplies the same zero command-clock
   input before Start. It advances no ticks and preserves logical counters.
   Count, bytes, queued output and ordered effects remain in exact equality;
   no new snapshot field or frozen expectation was masked.
2. The first explicit-module restart temporarily selected Rust options, which
   enabled Rust-only movement folding in relaxed mode. Program identity
   validation rejected it before sampling. The fix reuses the original native
   restart/options and asserts shared-program `Arc` identity before loading a
   variant. Existing physical graph optimizations remain enabled.

The explicit-module API and clock use are gated behind `plot-bench`; shared
library loading was factored without changing ABI/program validation. The
runner's full-vector assertion now reports the first difference rather than
dumping millions of words, while retaining exact equality. Five Python
self-checks cover reproducibility, bounds/duplicates, mutations, anchor drift,
geometric aggregation, incomplete results and correctness rejection.

| CPU family | This experiment's evidence |
| --- | --- |
| PM1 strict | Full frozen protocol and semantic snapshots pass; null-control timing is confounded |
| PM1 relaxed | Baseline full protocol and 51 partial-screen executions pass; no usable performance decision |
| ZCore strict | 26 builds/layouts; unchanged native/Rust state-and-motion differential passes through preparation + 5,000 ticks; no alignment timing comparison |
| ZCore relaxed | 26 builds/layouts checked; no new alignment timing comparison |
| ANPU | Holdout not started |
| BubbleSort | Holdout not started |
| CHUNGUS | Holdout not started |
| FPU | Holdout not started |
| PC_COUNTER | Holdout not started |

No four-workload geometric ranking, real mutations, frozen finalists,
seven-family confirmation or live publication/client comparison is claimed.
The previous strict ZCore +4.13% remains a prior observation, not a validated
production policy. The blocker is trustworthy timing attribution, not a newly
observed circuit correctness failure.

## Reproduction and retained evidence

The machine-readable decision, raw pairs, complete build manifests, costs and
checks are in `experiment4-alignment-search.json`. Compressed raw cohorts retain
the original runner results, including outputs/commands. The configuration,
clock/loader diagnostics, image diff, runtime probe and source provenance are
the accompanying `experiment4-*` files. Large immutable tables, DLLs, object
files, disassembly and full logs remain under ignored `work/alignment-search/`.

From PowerShell 7 at the repository root, use a fresh owned seed/output:

```powershell
. ./test_data/cpu-references/redpilator-next-env.ps1 "alignment-repro-$PID"
cargo build --release -p mchprs_core --features plot-bench --example bench_pm1_compare --example bench_engine_compare --locked
New-Item -ItemType Directory -Force work/alignment-repro | Out-Null
$env:MCHPRS_ENGINE_KERNEL_ANALYSIS='1'
(Get-Process -Id $PID).ProcessorAffinity=256
target/release/examples/bench_pm1_compare.exe test_data/cpu-references/pm1_redpilator_protocol.json work/alignment-repro/seed.json llvm 12070 1 --reference=test_data/cpu-references/pm1_redpilator_reference.json --publication=io
$seed=(Get-ChildItem "$env:MCHPRS_LLVM_WORK_DIR/mroww-native-cache" -Directory | Select-Object -First 1).FullName
py -3 tools/experiment_alignment.py selftest
py -3 tools/experiment_alignment.py build $seed test_data/cpu-references/experiment4-alignment-configurations.json work/alignment-repro/cache work/alignment-repro/manifest.json
# Finish all builds/tests before starting either calibration command.
./tools/experiment_alignment_calibrate.ps1 -Manifest work/alignment-repro/manifest.json -OutputPrefix work/alignment-repro/same -Mode same
./tools/experiment_alignment_calibrate.ps1 -Manifest work/alignment-repro/manifest.json -OutputPrefix work/alignment-repro/separate -Mode separate-high
py -3 tools/experiment_alignment_runtime.py work/alignment-repro/manifest.json work/alignment-repro/runtime-layout.json
```

`init` regenerates the 24 proposals; `schedule` writes paired module lists for
the existing runners; `aggregate`, `rank`, `mutate` and `confirm` implement the
bounded later stages. They were not used to manufacture leaders from the
confounded calibration. A controlled comparison separating artifact/mapping
effects from alignment is still needed before resuming those stages.
