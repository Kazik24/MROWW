# Experiment: compile one shared output opcode sequence

**Decision to make.** Can one straight-line native kernel for a recurring opcode sequence improve active relaxed PM1 throughput by at least 3%, preserve exact execution prefixes, and earn its place over the simpler split-range prototype? The deliverable is a reproducible experiment with a retain, reject, or inconclusive verdict. A production patch is conditional on the result.

**Why this experiment.** Relaxed PM1 has 34,614 distinct nonempty output ranges and 330 distinct opcode sequences. Its eight most frequent sequences by range count cover 30,085 ranges. The earlier split-range prototype removed 29.1% of record visits. Its median TPS gain of 4.61% falls within the variation observed for byte-identical DLLs. Select the new kernel using *executed* ranges and calibrate the timing setup independently. [Earlier verification and artifacts](../work/redpilator-opt-20261118/report.md)

## 1. Freeze the baseline and build a diagnostic

Use the same working-tree revision, compiler/linker binaries, flags, fixture, command configuration and projection mode for every variant. Record their hashes. Use fresh private output and LLVM cache directories. Baseline source in the earlier report was `db4b1531` plus its recorded working-tree changes. When using a newer checkout, rebuild all three variants from it.

Start with copied, validated PM1 native build artifacts and a small offline experiment driver under `tools/`. Reuse the build and optional profile-query pattern in the [previous prototype](../work/redpilator-opt-20261118/runtime.py) and the [runner hook](../work/redpilator-opt-20261118/profile-runner.patch). These are local experiment artifacts. Keep the inputs and commands needed to reproduce the new experiment with its report. The simulation runner already exposes an optional benchmark-only profile query.

1. Read the operation and component offsets/counts from the native descriptor. Iterate the 32-byte component records. At offsets 16, 20, 24 and 28, read little-endian `on_start, on_count, off_start, off_count`. Both starts are in operation records. For each nonempty range, check `start + count <= operation_count` using checked arithmetic. The sequence key is the ordered tuple of byte zero of each 8-byte operation. The operand is the little-endian u32 at byte four. Group equal keys across starts, edges and components. Preserve a mapping from each exact `(start,count)` to its sequence ID. [Encoding and validation](../crates/core/src/redpilator/sampled/llvm/encoding.rs)
2. Assign deterministic IDs in lexicographic tuple order. In the diagnostic DLL emit a per-component pair of IDs for off/on, using an empty sentinel. This larger diagnostic map is absent from timed binaries. Count calls by ID and edge at entry to `execute_output`. Report calls, records, distinct ranges and share of calls. Derive the existing two-pass classification from the same tuples for cross-checking.
3. Profile the complete relaxed PM1 SORT protocol. Reset counters after the single Start press, run 12,070 ticks, then read counters outside timing. Check the class totals against the previous census when using identical inputs and source. For example, it recorded 618,400 contribution-only calls on each edge. Collect a second trace with a different sampling offset to catch periodic sampling bias. Retain the frozen 1,534-message oracle. [PM1 runner](../crates/core/examples/bench_pm1_compare.rs) · [Protocol](../test_data/cpu-references/pm1_redpilator_protocol.json)
4. Rank by estimated time in each sequence's operation-processing loops. A sampled timestamp pair starts after range lookup and ends before the final `update_reactive`, including early exits. For sampled parents, subtract time spent in nested `execute_output` calls using a diagnostic call frame, or use an exclusive sampling profiler with equivalent attribution. Count every call, record sample counts, and estimate `calls × mean sampled time` per edge before summing edges. Handler work inside these loops also appears in the estimate. Treat it as an upper bound on removable work. Cross-check the leaders against whole-tick profiles and inspect which costs are dispatch versus the handlers themselves.
5. Freeze **one** nonempty sequence before candidate timing. Choose the highest estimated cost with repeatable ranking, observable dispatch work and a tractable straight-line body. As an experiment bound, consider lengths 1–16 and report excluded coverage. If even removing the entire winning loop share could not clear 3% TPS (about 2.91% of elapsed time), stop. If the time estimate is too uncertain to apply that bound, report the uncertainty. Record the selected tuple, reason and training trace. The interleaved example below illustrates lowering only.

## 2. Derive exact membership at build time

For the one frozen tuple `K` of length `L`, the candidate needs one **bit per operation start**, plus a length check. On the saved PM1 program, 199,501 operation records require 24,938 bytes. Components that share a range share the bit. Different operands at different starts can use the same kernel. No operand table or per-component dispatch table is needed in the timed DLL.

```text
bits = zeroed bytes of length ceil(operation_count / 8)
for (start, count) in unique on/off ranges:
    if count == L and opcodes(start, count) == K:
        bits[start >> 3] |= 1 << (start & 7)
```

Compute from the final validated blob after output-program sharing. For an embedded reactive module with any matches, emit the bitmap as an immutable byte array using the current C23 embed mechanism and define the selected length. Otherwise omit the array and dispatch entirely. Short-circuit on `count == L` **before** indexing the bitmap. Empty ranges can start at the one-past-end offset. The length guard also handles two valid ranges with the same start and different lengths. Self-check by enumerating every edge and comparing the compiled predicate to direct tuple equality. [Output-program sharing](../crates/core/src/redpilator/sampled/passes/share_output_programs.rs) · [Native table emitter](../crates/core/src/redpilator/sampled/llvm/generated.rs)

## 3. Lower to existing native operations

**Files and entry points.** Prototype in the copied `engine.c` and generated include. If retained, put the deterministic matcher/emitter in `GeneratedProgram::write` in [generated.rs](../crates/core/src/redpilator/sampled/llvm/generated.rs) and the native helpers/dispatch in [engine.c](../crates/core/src/redpilator/sampled/llvm/engine.c). Include the generated function at file scope after the operation and reactive helpers are defined, before `execute_output`. The existing `program.h` is included near the top of the translation unit and can supply the feature macro and bitmap. Use an embedded-only guarded include for the function body. Keep the retained tuple in the generator source so the existing generator/source cache hashing covers it. External training files must never silently affect a production cache key.

1. Factor the existing `execute_operation` body into an inline helper taking `(engine, opcode, operand)`. Keep the generic record decoder as a thin caller and keep all existing operation semantics in that helper. It increments `work_count`, then increments/checks `tick_operations`, then applies the operation. The failing instruction counts as work. Generate calls with a **literal opcode** and the live operand loaded by `read_u32(base + 8 * slot + 4)`. Check optimized machine code to establish that Clang eliminated dynamic opcode dispatch.
2. Extract the first-pass contribution handling into an inline helper shared by the generic loop and specialized kernel. For opcode 0, read the target from its link row and the old value from `contributions`. For opcode 1, use table 25's target and `arrangement_contributions`. Run the charged operation. Return immediately on error. After success, clear `updated[target]` when the stored contribution changed. Preserve this outer invalidation even when the underlying primitive also updates suppression state.
3. The generator makes two stable passes over **K at generation time**. Emit contribution slots (0 and 1) in increasing original slot order. Then emit remaining slots in increasing original slot order. For 4–10 and 13, retain the exact suppression check on table 43 at the live operand. A suppressed callback consumes no operation budget. Opcodes 2, 3, 11 and 12 bypass that check. Prefer a common inline suppression predicate used by both paths. Propagate the first nonzero status immediately, including errors from nested work. Leave each helper's own accounting in place.
4. In `execute_output`, after choosing the on/off start and count, dispatch once if the compiled length and bit match. Specialized success must reach `update_reactive(engine, component)` exactly once, even when all callbacks were suppressed. Generic reactive execution, empty ranges, the generic DLL and nonreactive modules keep their original routes. A shared finalization point or a direct return from the generated kernel can implement this. Pick one owner for finalization.

For `K = [0,0,0,2,3,0,2]`, the generated body must behave like this. Each slot loads **its own** operand from the actual range. Two slots can refer to the same link or target.

```text
stage link contribution at slots 0, 1, 2, 5, in that order
execute RefreshWire at slot 3
execute the watched-wire check at slot 4
execute RefreshWire at slot 6
run update_reactive for the calling component
```

Return on error after each step. Both edges can use the same body with different operands and different calling components. The prototype retains canonical operation records for snapshots, validation and fallback. There is no upfront range charge, callback sorting, mutable profile state in the timed kernel, or runtime code generation.

## 4. Prove the generated path is the path being tested

| Check | Fixture and acceptance condition |
|---|---|
| Membership | Cover zero operations, a one-past-end empty range, last valid start, the same start with two lengths, shared rise/fall, repeated tuples with unrelated operands, and a same-length near miss. Every bit-and-length dispatch agrees with exact bytecode matching. |
| Ordered effects | Exercise both edges, repeated operands, unchanged and changed contributions, a watched wire, and a nested callback. Compare the canonical and candidate status, contributions, suppression markers, pending work, ordered effects and work count. Verify the final reactive call gets the actual source component. |
| Failure prefixes | In diagnostic binaries inject remaining budgets 0 through the actual charged work of a small successful invocation plus one. Include failures during contribution staging, callbacks, nested work and final reactive processing where applicable. Also exercise a suppressed callback. Compare the complete state at the first failure, including the failed charge. Use a diagnostic hit counter to prove the chosen kernel ran. |
| Generator branches | In addition to the selected tuple, use synthetic test tuples containing opcode 1 and a suppressible callback to exercise the emitter's other branches. Exercise the generic and nonreactive builds. The previous synthetic harness stubs operation handlers, so also run through the real generated module and normal compiler entry point. [Native fixtures](../crates/core/src/redpilator/sampled/llvm/tests.rs) |
| Integration | Match relaxed and strict PM1 native snapshots, RAM, screen, lamps, halt and all ordered messages against the frozen protocol. Check strict ZCore screen and snapshot with the same frozen tuple. Compare live reset plus interpreted continuation. Label any workload on which the generated path has zero hits. |
| Code generation | Inspect the selected path in the optimized object, including inlined code. Locate the length/bitmap dispatch, literal-slot operand loads and calls to the expected handlers. Check that the specialized path has no generic opcode loop. Record `.text`, read-only data, actual bitmap bytes and native build time. |

## 5. Compare the alternatives and decide

Build **P** from untouched production, **S** from the previous split-range prototype, and **K** from production plus only the one selected kernel. K uses production fallback for every other range. Keep an additional refactor-only diagnostic build if common-helper changes alter fallback assembly. This distinguishes a kernel benefit from unrelated inlining or layout changes. Verify all builds use the same program identity and flags, and retain actual module hashes.

Use the scalar 12,070-tick active PM1 protocol with fixed command-clock input, command blocks enabled and zero timed publication. Build, run diagnostics and finish warmups before collecting timings. Use fresh identical executors, equal warmup per image, balanced load/allocation/execution order, and collect snapshots outside timed intervals. Reuse the [crossover design](../test_data/cpu-references/experiment4-image-crossover-preregistration.md). Its existing runner targets strict PM1 and identical images. Adapt and validate it for relaxed PM1 and distinct candidate hashes/RVAs before using it for candidate comparisons. Record the actual executed addresses. The candidate runs cannot establish calibration for themselves.

- **Calibration.** Run the same-image and byte-identical separate-image comparisons on the target setup. Apply the existing two-session ±1% interval criterion and order checks. Retain all raw pairs. A failed calibration allows correctness and code-generation conclusions and gives an inconclusive TPS verdict.
- **Benefit.** Freeze the tuple and timing schedule before the comparison. Run paired K/P and K/S trials on relaxed PM1, with at least five balanced-order pairs per comparison and fresh confirmation sessions. Report raw elapsed times, paired throughput ratios, geometric mean and uncertainty. Use the existing 3% target over P and 2% regression limit. Prefer S if K has no reproducible incremental advantage over S. Distinguish an unresolved difference from a demonstrated regression.
- **Adoption.** Require prefix parity, full protocol parity, verified native code, and a reproducible benefit before porting the private emitter to production. Recheck a retained Rust-generated module against the private prototype's membership bits, behavior and performance. Follow the existing project's wider fixture and live-publication checks before general adoption. [Engineering gates](REDPILATOR_OPTIMIZATION_PLAN.md)

### Runner commands

From the repository root on Windows, use the existing runners as follows. Pick a fresh experiment suffix and output directory per independent run. The last command is a correctness smoke run once P, S and K have been built. Its module list is a JSON array of absolute DLL paths in that order, all for the same relaxed PM1 program. Use the calibrated crossover for timing decisions.

```powershell
. ./test_data/cpu-references/redpilator-next-env.ps1 shared-output-01
$out = 'work/shared-output-01'
New-Item -ItemType Directory -Path $out -ErrorAction Stop | Out-Null
cargo build --release -p mchprs_core --features plot-bench --locked --example bench_pm1_compare --example bench_engine_compare
cargo test -p mchprs_core --lib redpilator::sampled::llvm --locked -- --test-threads=1
$env:MCHPRS_ENGINE_KERNEL_ANALYSIS = '1'
& ./target/release/examples/bench_pm1_compare.exe test_data/cpu-references/pm1_redpilator_protocol.json "$out/smoke.json" llvm 12070 3 --relaxed "--modules=$out/modules.json" --reference=test_data/cpu-references/pm1_redpilator_reference.json
```

**Agent handoff.** Return the reproducible experiment driver, one frozen opcode tuple and training table, cache/tool/fixture provenance, exact build and run commands, focused and integration results, disassembly excerpt, module size delta, and raw calibration and candidate timings. A clean stop after profiling or calibration is a valid result. Record the specific measurement that decided it.
