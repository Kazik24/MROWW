
# Redpilator: verified movement optimization plan

Status: batching and the bounded wire-deferral/invariant proposals were rejected. The bounded native kernel prototype was also not adopted: its strict gain over alignment alone was below the target, and relaxed performance regressed. No production optimization has been retained. The next proposed experiment is the offline stochastic tuner in [section 15](#15-offline-stochastic-optimization-experiments).

Results: [wire deferral](../test_data/cpu-references/experiment1-wire-deferral.md), [movement invariants](../test_data/cpu-references/experiment2-movement-invariants.md), and [native kernels/alignment control](../test_data/cpu-references/experiment3-movement-kernels.md). These decisions supersede the pending wording of the historical experiment instructions below.

The [census report](../test_data/cpu-references/digital-batch-census.md) found zero possible batches across all seven CPU families in both strict and relaxed modes, even with an eligibility upper bound. Do not implement the batching descriptors, checker or kernels described below. Sections 1–12 retain the original proposal and its reasoning; section 13 supersedes its implementation sequence.

## 1. Goal

Replace expensive movement bookkeeping with cheaper operations whose effects can be checked against the original sequence. Preserve the behavior of each supported execution mode, existing tests, benchmark circuits and interpreter handoff.

The original proposed first rule combined updates to the active-source count of one digital input, followed by a small rule-specific checker and Rust/LLVM kernels. Its census rejected implementation. Future proof infrastructure should follow a separately demonstrated opportunity rather than depend on this rejected rule.

The retained [LLVM investigation](../test_data/cpu-references/llvm_contract_investigation.json) reports an older ZCore profile with roughly 59% of sampled work in movement and input accounting, versus roughly 2.5% in output dispatch. This motivates investigating movement; it does not establish the current bottleneck or the prevalence of eligible batches.

Success means a measured throughput improvement with equivalent behavior, acceptable compilation cost and bounded metadata. Fewer instructions in an intermediate representation alone are insufficient.

## 2. Existing implementation to reuse

Paths are relative to `crates/core/src/redpilator/`.

| Code | Relevant responsibility |
| --- | --- |
| [sampled/movement.rs](../crates/core/src/redpilator/sampled/movement.rs) | `ArrangementFlip`, movement lowering and validation |
| [sampled/fold_movement.rs](../crates/core/src/redpilator/sampled/fold_movement.rs) | Equivalence-based folding of movement states |
| [sampled/input.rs](../crates/core/src/redpilator/sampled/input.rs) | Digital active counts and analog histograms |
| [sampled/executor/movement.rs](../crates/core/src/redpilator/sampled/executor/movement.rs) | `run_move_steps` and `replay_movement_flips` |
| [sampled/program.rs](../crates/core/src/redpilator/sampled/program.rs) | Immutable program ownership and validation |
| [compiler/compilation.rs](../crates/core/src/redpilator/compiler/compilation.rs) | Mode transforms before runtime creation |
| [sampled/llvm/generated.rs](../crates/core/src/redpilator/sampled/llvm/generated.rs) | Generated circuit tables, identity and existing static classification |
| [sampled/llvm/llvm_movement.c](../crates/core/src/redpilator/sampled/llvm/llvm_movement.c) | Native movement replay and contribution accounting |

Both executors already recognize complete fixed movement ranges that fit the remaining operation budget. They validate group transitions, advance groups, charge the range's original cost, then replay its flips. When this path cannot be used, existing scalar execution preserves partial progress and failure behavior.

Native generated modules also have filtered movement graph paths. Those paths have their own physical records and logical costs. Version one of this plan optimizes only canonical fixed-range replay; it leaves existing graph replay selected wherever it is currently selected. Do not disable an existing optimization to create opportunities for the new one.

Already implemented optimizations include cached token contributions, fixed movement ranges, analog-input classification, relaxed movement folding and selective wire work. Do not count these as new work or duplicate them.

## 3. Stage zero: measure eligible work

Before implementing a rewrite, add temporary or benchmark-only census instrumentation:

1. Enumerate the actual fixed ranges used for token departure and arrival.
2. Apply the eligibility rules in section 4 without changing execution.
3. Record eligible run counts and length distributions, original flip counts and estimated plan bytes.
4. Count dynamic executions of eligible runs separately for Rust, native canonical replay and native graph replay. A large static table can be cold.
5. Record how often the full-range guard fails and how often an eligible run's contribution values actually change.

Collect this for PM1, ZCore, ANPU, CHUNGUS, BubbleSort, FPU and PC_COUNTER, using their existing inputs and protocols. Measure strict and relaxed modes separately where supported.

Proceed only if eligible runs occur often enough to plausibly matter. If most relevant work uses native graph replay or analog inputs, report that result and revise the rule before implementing the kernels. This is a go/no-go gate, not a requirement to force the proposal into production.

Keep census instrumentation outside timed performance runs. Do not add a player-facing option for it.

## 4. First rewrite: digital contribution batching

### 4.1 Eligibility

A candidate is a contiguous run within one already validated canonical fixed flip range. Require all of the following:

- At least two flips; use a higher retention threshold only if measurement supports it.
- Every flip targets the same component and its main input channel (`side == false`).
- The target is a fixed component with a stable kind and a digital input. The initial whitelist is redstone lamps, hoppers, iron trapdoors, note blocks, copper bulbs, repeaters, standing/wall torches and observers. Exclude wire, comparator and piston targets. Check these kinds against the existing runtime input allocation; remove any kind whose invariants cannot be established.
- The target's runtime input has no analog histogram. Derive this from the same program facts used by runtime initialization; do not infer it merely from a zero initial signal.
- Every source is a token with a validated immutable `token_contribution`. Exclude component sources, routed signal lookups and any source requiring a runtime read.
- Contribution link IDs are distinct within the run.
- Each link agrees with its flip's target, channel, source and other canonical metadata.
- There are no callbacks, scheduler operations, world writes or external reads inside the run. The current flip primitive satisfies this restriction; future changes to it must revalidate the rule.
- Runtime count bounds, index bounds and switched-list capacity are established by program/runtime invariants.

Mixed arrival and departure flags are allowed if every other condition holds. Their exact meanings are handled below.

Do not reorder flips, cross a range boundary, merge different targets, or infer that a callback is harmless because current fixtures do not expose it.

### 4.2 Original state transition

For flip `i`, define:

```text
l_i = its arrangement-link ID
c_i = its immutable attenuated token contribution, in 0..15
v_i = c_i if entering, otherwise 0
o_i = contribution[l_i] before the run
b(x) = 1 when x != 0, otherwise 0
A = target's active-source count before the run
```

The scalar loop writes `v_i` to each link and applies:

```text
A <- A - b(o_i) + b(v_i)
```

An unchanged value may take an early exit in the current helper, but the equation still holds.

Each flip also:

1. Clears the target's update-suppression marker when `c_i != 0`.
2. Enqueues the target in the switched list if it is not already present.
3. Contributes one unit to the original logical work accounting.

The suppression condition uses `c_i`, not `v_i`. In particular, departure of a powered token can clear suppression even though the stored new contribution is zero. This distinction must appear in the checker and tests.

### 4.3 Replacement algorithm

Precompute these descriptor constants:

```text
added = sum(b(v_i))
invalidate = any(c_i != 0)
cost = number of original flips
```

At runtime, inside the existing successful full-range fast path:

```text
removed = 0
for flip in the candidate's original order:
    removed += b(contribution[flip.link])
    contribution[flip.link] = flip.token_contribution if flip.entering else 0

target.active = target.active - removed + descriptor.added

if descriptor.invalidate:
    updated_in_phase[target] = 0

if not switched[target]:
    switched[target] = true
    switched_list.push(target)

account for descriptor.cost using the current backend's accounting convention
```

Keep every individual contribution value: later movement, input reads and reset depend on them. The saving is repeated count writes, classification and suppression/list bookkeeping. There is still a loop over link slots.

Use subtraction followed by addition with the proven count bounds. Do not implement an unsigned subtraction of `added - removed`, which can wrap when the net change is negative.

The existing caller has already charged the complete range's tick budget. The Rust kernel must still account for its ordinary completed-operation counter, as the original unguarded replay does. The native caller already charges both `tick_operations` and `work_count` for the full range. Do not charge them again in the native batch kernel.

### 4.4 Example

Two distinct links feed the same digital receiver:

```text
old contributions: [15, 0]
new contributions: [0, 15]
other active sources: 3
initial active count: 4

scalar count: 4 -> 3 -> 4
batch count: 4 - 1 + 1 = 4
```

The intermediate count is unobservable inside this eligible region because no operation reads it or delivers a callback. The link values and all bookkeeping must still match when the region ends.

## 5. Proof contract and checker

### 5.1 What is being proved

For every valid runtime state satisfying the candidate's preconditions:

```text
execute_original(region, state) == execute_replacement(region, state)
```

Equality at the region boundary covers:

- All contribution slots, including untouched slots.
- Input counts and untouched histograms.
- Update-suppression markers.
- Switched membership and exact list order.
- Logical operation counters.
- Unchanged scheduler, piston, command, note, publication and world state.

The proof is local and conditional on explicit invariants. It is not a proof of the entire simulator, vanilla compatibility, or the correctness of arbitrary generated C code.

### 5.2 Algebraic argument

Because link IDs are distinct, each `o_i` is read from its original slot and is unaffected by earlier writes in the run. Therefore:

```text
A_final = A_initial + sum(b(v_i) - b(o_i))
        = A_initial - removed + added
```

The valid-state invariant says the input count includes every nonzero contribution being removed. Thus `A_initial >= removed`. The final count is bounded by the validated number of contributors to the input, which must fit its counter type.

All scalar suppression writes assign the same value to the same marker. Their combined condition is exactly `any(c_i != 0)`.

All flips refer to the same non-piston target. If the target is absent from the switched list, scalar execution appends it once. Otherwise it appends nothing. No other target is processed inside the candidate, so appending once at the replacement's end preserves list order relative to preceding and following regions.

The target is digital, so neither execution touches histogram storage. Token contributions are immutable, so the run does not depend on a state read that its own writes could change.

These arguments justify the rewrite without enumerating whole circuit states.

### 5.3 Checker algorithm

The optimizer may propose a candidate, but it cannot construct a verified descriptor directly. A separate checker must:

1. Check the proposed interval lies within the original replay range and has the required length.
2. Read the original flips itself and independently check every eligibility condition.
3. Check link uniqueness using a bounded temporary set.
4. Recompute `added`, `invalidate`, target and logical cost from the originals. Verify each cached token contribution against the token's immutable output and the flip's attenuation, rather than trusting the cached byte alone.
5. Establish the count/capacity invariants from validated program metadata.
6. Construct a verified descriptor containing only these recomputed values.

Keep the descriptor constructor private to the checker. In Rust, use a concrete `VerifiedDigitalBatch` type. Avoid a generic expression language or solver interface until a second useful rule needs one.

The trusted code consists of the checker, the rule's algebra, and the Rust/C kernels implementing it. Independent scalar comparisons test that those implementations match the proof. Test success alone must not be described as a universal proof.

### 5.4 Failure equivalence

Version one runs only after the existing full-range guard succeeds. That guard establishes valid group transitions, no existing failure, and enough remaining tick budget for every original flip in the enclosing range.

If the guard fails, run the current scalar path with no new state mutation, counter charge or plan preparation at runtime. This preserves the exact successful prefix, error location and backend-specific counter behavior.

Do not attempt to fast-forward part of a batch near the operation limit. Do not unify Rust and native error accounting merely because their conventions differ.

## 6. Planner and storage

Add one private module, proposed as `sampled/optimize_movement.rs`, owning discovery, descriptors, the checker and the first rule. Keep runtime execution in the existing executor movement module.

Use a small plan representation:

```text
MovementPlan: range of plan steps for one canonical fixed replay range
PlanStep:
    OriginalRange(original flip range)
    DigitalBatch(VerifiedDigitalBatch)
VerifiedDigitalBatch:
    original flip range
    target
    added nonzero contributions
    invalidate marker flag
```

Derive cost from the original range length. Reuse canonical flip records for link IDs and new values; do not duplicate complete instruction buffers. `Program` owns immutable plans. Runtime instances share them and allocate no plan data while ticking.

Planner algorithm:

```text
for each unique referenced canonical fixed range:
    cursor = range.start
    while cursor < range.end:
        discover maximal eligible same-target run starting at cursor
        stop at the first incompatible flip or repeated link ID

        if run meets minimum length and checker accepts:
            emit DigitalBatch(verified descriptor)
            cursor = run.end
        else:
            emit OriginalRange(cursor .. cursor + 1)
            cursor += 1

    merge adjacent OriginalRange steps
    retain the plan only if it contains a useful verified batch
```

Processing the first incompatible record again on the next iteration ensures it is never lost. Plans must partition their source range exactly once, in order: no holes, overlap or duplicated operations. Add this validation separately from batch verification.

Deduplicate plans for identical range keys. Do not rescan all prior links for each record: the run-local uniqueness set gives expected linear scan cost. Bound total work by the sum of scanned unique range lengths, and measure overlap amplification.

Use named compile-time limits for optimization work and metadata, chosen from the census. Exceeding those optimization limits leaves the remaining ranges canonical; it must not reject a circuit. Distinguish this from explicit cancellation, which must continue to cancel compilation through `TaskMonitor`.

## 7. Compilation and backend integration

### 7.1 Build plans after transformations

In `compiler/compilation.rs::compile_program`, build plans after relaxed-mode changes and movement folding, before wrapping the completed `Program` in `Arc`. Final movement ranges and target classifications must be settled first.

Intermediate `Program` instances used by compiler probes remain valid without plans. Public compile paths produce plans once; an absent plan means ordinary replay.

Any later operation that rebuilds movement tables must clear existing plans before changing their referenced records. Tests must exercise rebuilds and repeated optimization. A second optimization call should be deterministic and should not layer plans over plans.

### 7.2 Rust

Inside `run_move_steps`, retain the existing fast-path eligibility and group updates. Replace only its unguarded replay call when an associated plan exists:

- `OriginalRange` uses existing `replay_movement_flips::<false>`.
- `DigitalBatch` uses the specialized kernel from section 4.
- The complete range's tick charge remains at its current location.
- A missing plan or failing full-range guard uses existing behavior.

Keep notification delivery, shape refresh, switched-node processing and watched-wire processing after replay exactly where they are now.

### 7.3 LLVM

Emit plan descriptors as immutable circuit data in `generated.rs`. Use the same checked plans as Rust, lowering indices with checked conversions.

Add native descriptor validation for bounds, plan coverage, target/channel classification, distinct links and recomputed batch constants. Validate against canonical encoded flips; an emitted flag by itself is not evidence. Run validation once during module/state setup, not per tick.

Integrate the kernel only into the canonical fixed-range loop in `llvm_movement.c`. Keep native graph replay, scalar fallback, materialization and group-transition handling intact.

Include all emitted plan data and the optimization format revision in module identity. Update the appropriate native semantic/format version and loader checks when the contract changes. Old artifacts must not load under a new incompatible contract. Avoid changing the external ABI if generated private tables suffice.

Generic diagnostic modules and programs without retained plans continue to use canonical replay. Never silently disable the existing graph optimization to route a circuit through the new kernel.

Optimizing graph replay is a later rule requiring a separate proof over its filtered physical records, original logical costs and materialization behavior.

## 8. Correctness tests

### Checker tests

- Accept valid all-entering, all-leaving and mixed runs, including zero token levels.
- Reject repeated link IDs, different targets, side inputs, component sources and invalid ranges.
- Reject analog targets, pistons, movable/unstable target kinds and inconsistent link metadata.
- Mutate every descriptor field and prove the checker recomputes or rejects it.
- Reject plans with missing, duplicated or reordered records.
- Test cancellation, optimization budget exhaustion and idempotent rebuilding.

### Kernel differential tests

Build identical runtime states and execute the original and replacement region independently. For two links, enumerate all old/new strengths in `0..15` and several background-contributor counts. Test both values of switched membership and suppression markers, with an existing switched-list prefix and a following different target.

The previous research check enumerated 196,608 count-only cases. Turn this into a real test of contributions, counters, markers and list contents. Add generated runs of varying lengths and compare every relevant field to the scalar oracle.

Include powered departures explicitly: `new == 0` can still require suppression invalidation. Include unchanged contributions, since existing helpers already optimize those cases.

### Failure tests

For a small enclosing movement range, place the tick-operation counter at every boundary from insufficient budget through exactly sufficient budget and one unit beyond. Compare complete state, logical counters, error and position against canonical execution.

Include preexisting failure, invalid group transitions and unrelated work before/after the candidate. No batch should execute when the current full-range path would reject the range.

### Integration tests

Run original and optimized programs through the same inputs in Rust and LLVM. Compare per-tick state, ordered pending ticks, piston motion, notes and command output. Publish in each supported mode, reset during movement, then continue with the interpreter.

Keep each strict/relaxed mode's own baseline. This pass must preserve relaxed behavior too; it does not establish equivalence between relaxed and strict modes.

Run existing fuzz, RedstoneDB, observer/BUD, operation-limit, cache and handoff coverage. Preserve all frozen expectations. Include native malformed-descriptor and stale-artifact rejection tests.

## 9. Benchmark gates

Use the existing fixture loaders and benchmark examples. PM1's current `pm1_redpilator` protocol and older PM1 SORT replay are distinct workloads; retain their separate identities and inputs.

| Workload | Required checks |
| --- | --- |
| PM1 | Current protocol outputs, ordered messages, halt state and applicable frozen replay |
| ZCore12B | Screen/output trace, execution state and reset continuation |
| ANPU Pong | Recorded frames, scheduled work and piston behavior |
| BubbleSort | RAM, output and halt checkpoints |
| CHUNGUS | Existing Mandelbrot benchmark outputs |
| FPU | Arithmetic output protocol |
| PC_COUNTER | Counter output protocol |

Use a benchmark/test-only way to disable the pass on an otherwise identical program. Exclude only derived plan data from semantic state comparison; compare real simulation state and logical work exactly.

Benchmark procedure:

1. Record commit, fixture hashes, mode, compiler versions, flags and machine.
2. Use identical initial states, stimuli, warmup and tick counts for each pair.
3. Run at least five alternating baseline/candidate pairs, reversing order across pairs.
4. Keep thread affinity and competing load controlled. Repeat noisy results.
5. Measure kernel throughput separately from live publication/client delivery.
6. Record cold compilation time, peak memory, native artifact size, plan bytes and dynamic batch hits.
7. Verify outputs and state outside the timed window; run detailed per-tick comparisons separately.

Initial acceptance policy: target at least a 3% repeatable median kernel improvement on a primary CPU with substantial eligible work, and no repeatable regression greater than 2% on any supported benchmark/mode. These are proposed engineering gates, not predicted gains. Investigate results near the noise floor with more pairs rather than passing them automatically.

Require no correctness differences, no newly rejected circuits, and acceptable compile-time/memory overhead. A workload with no useful plans should have negligible added runtime cost. If plan dispatch costs more than it saves, raise the retention threshold based on measurements or discard the rule.

## 10. Implementation sequence

1. **Census:** measure static and dynamic eligible work; decide whether this rule is worth implementing.
2. **Checker:** add descriptors, eligibility, plan coverage and independent rejection tests. No runtime change yet.
3. **Rust kernel:** integrate only the existing successful fixed-range path; complete state and failure-boundary differential tests.
4. **Native kernel:** emit and validate tables, update identity/version handling, and test against native canonical execution and Rust.
5. **Regression gate:** run focused tests, full relevant integration coverage and explicit long CPU replays.
6. **Performance gate:** collect paired results and retain only demonstrably beneficial plans/rules.
7. **Documentation:** add a short description to the architecture guide only after adoption. Store reproducible measurements under `test_data/cpu-references/` and generated output under an ignored directory.

Useful existing checks:

```sh
cargo test -p mchprs_core --lib redpilator --locked
cargo check -p mchprs_core --all-targets --all-features --locked
```

Use the [README testing instructions](../README.md#testing) and existing CPU test/example entry points for the longer runs. Inspect test names and ignore reasons; do not run every ignored test indiscriminately, since some are capture/export tools.

## 11. Later rules

These remain possible extensions requiring independent coverage and performance evidence; they do not justify continuing the rejected batching implementation:

- Extend batching to other callback-free regions or native graph records with separately checked accounting/materialization semantics.
- Reuse dynamic source reads when the checker proves that no intervening write can affect them.
- Aggregate analog histogram changes when exact bucket counts and occupied-level masks can be proved equivalent.
- Remove redundant writes only when all subsequent reads and failure boundaries remain equivalent.

Keep notification coalescing, global state-space search, general SMT integration and arbitrary generated replacements outside the first implementation. Each adds a substantially larger proof obligation. Add another rule because it removes measured hot work, not merely because the engine can express it.

## 12. Completion criteria

- The optimizer proposes; the checker independently validates and constructs executable descriptors.
- Optimized and canonical paths agree on state, ordered effects, work accounting and failure prefixes.
- Unproved or unprofitable candidates retain canonical execution.
- Both backends and cache identities handle plans consistently.
- Existing benchmark CPUs and supported modes pass their unchanged oracles.
- Repeatable timing evidence justifies enabling the rule.
- The implementation adds no dependency or public configuration without a demonstrated need.

If the census or benchmarks reject this candidate, keep the findings and stop. That is a useful outcome; it avoids maintaining a proof system and execution path that do not improve the server.

## 13. Next experiments: run sequentially

Complete each experiment's census, correctness assessment and retain/reject decision before starting the next. A documented no-go is a successful result. Do not combine candidates into one patch or benchmark several candidates together.

### 13.1 Establish a trustworthy baseline

The census report records 279 passing tests, 54 failures and 30 ignored tests. LLVM preparation and plot activation failures are unresolved. Resolve or clearly establish these baseline failures before changing runtime behavior.

1. Reproduce an isolated native preparation failure and `executed_plot_reuses_exported_state_and_keeps_pending_ticks_once`.
2. Inspect Clang/LLD discovery, `MCHPRS_LLVM_CLANG`, `MCHPRS_LLVM_LINKER`, DLL overrides and cache ownership. Earlier work in this session successfully used Unity's bundled Clang and Rust's bundled `lld-link` through explicit paths. Verify their current availability and compatibility; this is a lead, not proof that every failure is environmental.
3. Use a dedicated owned `MCHPRS_LLVM_WORK_DIR` to avoid conflicts with other processes. Do not clear another process's active cache.
4. Determine whether plot activation errors follow native preparation failure or have an independent cause. Keep baseline fixes separate from optimization changes.
5. Rerun the focused failures, full Redpilator suite and all-target/all-feature check. Do not skip failing tests, weaken assertions or alter frozen expectations to establish a green baseline.
6. Record tools, commands and remaining failures. If execution remains blocked, finish useful static census work, then stop before runtime optimization or performance claims.

The existing 1,598.53 ticks/s ZCore median is strict Rust and exploratory: builds and other work overlapped samples. Do not use it as a controlled A/B baseline or compare it directly with native throughput. Preserve the distinction between the current PM1 protocol fixture and the separately rejected legacy PM1_SORT fixture.

### 13.2 Experiment 1: per-wire deferred state

**Question:** does circuit-wide lazy-wire eligibility prevent useful optimization of individual safe wires?

**Scope:** relaxed execution only. Begin with unwatched wires with no electrical, side-input, reactive or notification consumers. Preserve the current whole-circuit and native graph fast paths. Strict semantics, observer-sensitive wires and general removal of unpublished state are outside this experiment.

**Census algorithm:**

1. Inspect `GeneratedProgram::with_tick_capacity`, `wire_replay_graph`, native wire reads and publication/materialization paths.
2. Enumerate all readers of each wire's state, including movement, callbacks, reactive processing, snapshots and reset.
3. Build a conservative individual-wire eligibility predicate. An unclassified reader makes a wire ineligible.
4. Count additional eligible wires separately from those already optimized. Record why the whole-circuit predicate currently rejects each affected circuit.
5. Measure how often additional candidates execute expensive updates on the benchmark protocols. Separate static coverage from dynamic work.
6. Estimate metadata, eligibility checks and materialization cost. Stop if additional candidates are absent, cold or unlikely to repay that overhead.

**Implementation if justified:**

- Define a compact representation of deferred state and every boundary that requires materialization.
- Prove that omitted work cannot affect execution and that materialization reproduces the existing relaxed baseline. Current source strength alone may not reconstruct the wire's refresh history; retain required history or reject that candidate.
- Use the smallest immutable eligibility metadata and bounded runtime bookkeeping that satisfy the proof.
- Keep source/output changes, watched-wire behavior, pending work and publication timing consistent with the baseline.
- Update native validation and module identity/versioning if the representation changes.
- Check full snapshots, every publication mode, operation-limit failure prefixes, cache reuse and interpreter continuation after reset.

**Decision:** retain only after paired performance and correctness gates pass. Reject the candidate if per-wire checks or reconstruction erase its benefit. Conclude this experiment before starting invariant specialization.

### 13.3 Experiment 2: bounded movement invariant specialization

**Question:** can proven constraints simplify connections or movement tables beyond the existing equivalence folding and compaction?

**Scope:** small existing movement groups under explicit analysis-time and memory budgets. Start with one invariant family and one corresponding rewrite, such as a connection that is constant across every reachable local state. Preserve token identities and callback ordering. Exclude whole-CPU state exploration and assumptions inferred solely from recorded traces.

**Census and proof algorithm:**

1. Inspect existing groups, transition tables and folding to identify potential unreachable combinations or constant connections that are not already removed.
2. Build a finite, conservative local transition model. Include admitted external input changes, pending work and intermediate piston motion. When outside dependencies are uncertain, allow all their possible values; do not silently freeze them at captured values.
3. Construct the initial local-state set from the actual captured state and admitted uncertainty. It must include every local state possible at activation.
4. Compute reachable-state closure with a worklist. Add every successor allowed by the conservative model. Stop analysis and retain canonical code if its budget is exhausted.
5. Propose an invariant only over the completed closure. Independently check that the initial set satisfies it and every modeled successor preserves it.
6. For a constant-connection candidate, check that every reachable state produces the same connection value and required notification behavior. Electrical equality alone does not authorize removing notifications.
7. Specify the rewrite's state reconstruction, ordered effects and logical cost. Recheck the transformed program against that contract before making it executable.

If the model cannot conservatively represent relevant timing or movement, reject the group. Benchmark observations may prioritize groups but cannot prove reachability restrictions.

**Implementation if justified:** specialize only the proved connection or redundant state dimension. Keep canonical fallback for unproved groups. Derive metadata after all mode-dependent transformations, invalidate it when those tables change, and include changed native data in artifact identity and validation.

**Decision:** require reduced dynamically executed work or repeatable throughput improvement. Smaller tables alone are insufficient. Reject analysis that costs too much compilation time or memory for its runtime benefit.

### 13.4 Shared gates and experiment records

Apply the correctness and performance principles in sections 8–9 to each candidate, replacing batching-specific tests with tests of that candidate's proof contract.

- Compare enabled and disabled variants of the same source revision and otherwise identical programs.
- Preserve each strict/relaxed mode's own baseline and unchanged fixture oracles.
- Cover PM1, ZCore, ANPU, BubbleSort, CHUNGUS, FPU and PC_COUNTER; report baseline admission failures separately.
- Compare full simulation state, ordered pending ticks, piston motion, command/note effects, failure prefixes and reset continuation. Final lamp agreement alone is insufficient.
- Use at least five alternating baseline/candidate pairs with identical warmup, stimulus, tick counts, affinity and publication settings. Run one benchmark at a time, without overlapping tests, builds or census work.
- Separate kernel throughput from live publication and delivery. Record compilation time, peak memory, artifact/metadata size and dynamic coverage.
- Retain the proposed gates of at least 3% repeatable gain on a primary affected CPU and no repeatable regression greater than 2% elsewhere. Investigate noisy or marginal results rather than automatically passing them.
- Run long CPU regressions explicitly. Inspect ignored-test descriptions so capture/export tools are not executed accidentally.

For each experiment, save a concise report and machine-readable results under `test_data/cpu-references/`. Record source revision, variant differences, fixture hashes, tools, commands, static/dynamic opportunity counts, proof obligations, correctness results, raw paired timings and an explicit **retain**, **reject** or **blocked** decision.

Keep production changes only for candidates that pass both correctness and performance gates. Preserve useful negative measurements. Do not add generic proof infrastructure merely to continue after a rejected candidate.

## 14. Deeper ideas retained for later investigation

These are recorded research directions, not additional experiments to execute before section 13 is complete:

- **Broader reconstructible-state elimination:** represent a region using source state and arrangement IDs, deriving intermediate state when needed. Require a reconstruction relation that preserves future behavior, ordered effects, logical cost and failure boundaries. Per-wire deferred state is the bounded first investigation of this direction.
- **Verified subcircuit state machines:** replace a small bounded region with a transition function from local state, incoming event and timing phase to new state and ordered outgoing events. Start with small acyclic regions. Settled-output truth tables are insufficient when glitches, scheduling or external-event interleaving are observable.
- **Guarded execution specialization:** generate a bounded specialized sequence when proven preconditions hold, with canonical fallback before any mutation when they do not. Guard source stability, connectivity and original operation cost. Measure guard overhead and code-size/instruction-cache costs before retaining it.

General SMT integration, global state-space exploration and cross-target notification coalescing remain deferred. A specific measured opportunity must justify their larger correctness and maintenance costs.

## 15. Offline stochastic optimization experiments

Randomness selects build candidates offline. The selected production implementation remains deterministic, with normal validated artifact identity. Do not tune on live player plots or add runtime random decisions.

### 15.1 First choice: randomized helper-alignment search

Experiment 3 found that aligning the generic flip helper to 64 bytes improved strict ZCore throughput by 4.13% with 16 extra code bytes. This is one machine/workload result, not a general alignment policy. The generated kernels added only 1.46% over that control while adding about 9 KB. Investigate layout independently before adding more specialization.

Reuse `tools/experiment_movement_kernels.py`, the explicit LLVM environment and the existing paired benchmark runner. Keep this benchmark tooling; do not add an autotuning dependency for a small discrete search.

**Initial search space:** alignment of up to four hot helper functions, selected from a current profile. Candidate names to inspect include `apply_movement_flip`, `set_arranged`, `execute_output` and `drain`. Each parameter takes `default`, 16, 32, 64 or 128 bytes. Confirm each function is emitted and inspect actual linked addresses: an attribute on an inlined-away function may have no effect.

Version one changes only function alignment. Keep the native compiler version, optimization level, aliasing assumptions, arithmetic rules, instruction order, batch size and generated tables fixed. Function ordering, inlining and loop-unroll choices are separate later search spaces, not additional dimensions in the first experiment.

**Bounded algorithm:**

```text
references = [production baseline, existing flip-helper alignment-64 control]
evaluate references under the same current environment

candidates = 24 unique random configurations from a recorded PRNG seed
evaluate candidates in randomized blocks with interleaved references
retain the best 4 provisional configurations

generate up to 8 new configurations by changing one parameter of a survivor
evaluate with the same protocol
freeze at most 3 finalists before long validation
```

The default budget is 32 distinct search candidates plus the two references. Record attempted duplicates and build failures; do not silently extend the budget. Cache builds by source, program, tools and configuration identity. Build the round before timing it; never overlap builds with measurements. Repeated evaluation of the same configuration should reuse the same artifact.

Inspect disassembly and actual function offsets to identify ineffective changes. Record `.text` size, native compile/link time and metadata alongside measurements. Apply an initial hard `.text` growth cap of 4 KiB per module for this alignment-only experiment; this is a proposed experiment limit, not a measured optimum.

**Screening:** use PM1 and ZCore, strict and relaxed, as four separate training workloads. Use identical frozen stimuli and preparation, then short runs with at least three alternating paired measurements per configuration. Short results rank candidates only. Near ties remain ties; allocate repeat measurements to uncertain finalists rather than treating one fast sample as a win.

**Objective:** maximize the geometric mean of throughput ratios across those four workloads, subject to correctness, code-size limits and per-workload regression gates. Record every workload separately so a large win cannot hide a regression. Compare against both production and the alignment-64 control. Prefer the smaller/simpler configuration when measured gains are indistinguishable.

**Confirmation:** freeze finalists, then run fresh long measurements with at least five alternating pairs per supported workload/mode. Use new measurement sessions and unchanged profiles. Include all seven CPU families, their original oracles, full relevant regression coverage, and live publication/client costs before general adoption. The five non-training CPU families are holdouts: do not repeatedly tune against their results and continue calling them holdouts.

Keep inference honest: searching many candidates tends to select favorable noise. The search-round winner is only a proposal. Use independent confirmation, report distributions and paired uncertainty, and investigate borderline results. Preserve the existing 3% gain/2% regression engineering gates. More complex candidates must also justify their cost relative to the simplest validated alignment control.

If only a narrow machine/compiler/workload benefits, report that scope. Do not claim general hardware portability. Record the target architecture and toolchain, and keep a generic fallback if a target-specific policy is eventually adopted.

**Stop:** reject the search result if fresh confirmation loses the gain, regressions exceed the gate, or the current alignment-64 control remains equally effective with less complexity. A no-go does not justify expanding the search indefinitely.

**Experiment 4 outcome (2026-11-16): blocked at timing calibration.**
See [the alignment search report](../test_data/cpu-references/experiment4-alignment-search.md)
and its machine-readable measurements. All 24 seeded configurations plus both
controls built for the four training programs; maximum `.text` growth was 208
bytes. In strict PM1, production and flip64 images differ only in one COFF
timestamp byte, yet separate-image full-protocol comparisons showed apparent
median gains of +2.93%, +1.42% and +1.40%. Equal warmup and higher process
priority did not eliminate the offset/outliers. The cause remains unresolved;
the loaded-address probe does not establish ASLR causality. The current method
cannot attribute gains at the 3% / 2% gates reliably. No valid four-workload
ranking, real mutations, finalists, holdout or live-client validation followed.
Production alignment remains unchanged, and the earlier ZCore +4.13% is still
an unvalidated candidate observation. Resolve this calibration before resuming
the bounded search; no generated-kernel selection or semantic experiment was
started.

### 15.2 Second choice: randomized selection of generated kernels

This searches which existing exact-sequence kernels to emit, not their semantics. It is lower priority because experiment 3 found low coverage and a relaxed regression.

Represent a candidate as a subset of profiled physical ranges plus bounded dispatcher parameters. Mutations add, remove or swap a range. Respect total emitted-operation and measured code-size caps; use frozen training profiles and retain the generic path for all unselected ranges.

Start with random subsets weighted by dynamic coverage, followed by local mutations of the best few candidates. Evaluate actual whole-kernel runtime; summed instrumented TSC costs are only a proposal heuristic. The objective must include dispatch overhead and comparison with the validated alignment policy. Keep physical graph records and original logical costs distinct.

Proceed only if a coverage/code-size study suggests a materially better frontier than the previous top-16 selection. Do not assume a genetic algorithm can compensate for diffuse profiles. Freeze selected kernels before held-out correctness and performance runs. Do not run this search concurrently with alignment tuning; hold layout policy fixed.

### 15.3 Later: stochastic search over proved local rewrites

For a measured hot, bounded primitive, randomly propose alternatives from a small typed expression language. Start with pure integer input/output calculations; exclude arbitrary pointer mutation, callbacks and scheduling.

Pipeline:

```text
generate candidate
  -> cheap differential counterexample tests
  -> independent exhaustive or bit-vector equivalence check
  -> compile and measure only proved candidates
  -> confirm finalists in full runtime workloads
```

Model exact bit widths, overflow, shifts and preconditions. Treat proof timeout or unknown as rejection. If memory effects are later admitted, the proof must include aliasing, permitted writes, errors and all observable effects. Tests cannot substitute for universal equivalence, and an abstract proof must correspond to the actual lowered implementation.

Use simple random search first. Simulated annealing could later explore one-mutation neighbors when measured evidence shows local search getting stuck; genetic populations or Bayesian search require a demonstrated benefit over the simpler search. Every variant needs fixed seeds, finite budgets and independent confirmation.

This direction is inspired by [STOKE's stochastic search and equivalence infrastructure](https://github.com/StanfordPL/stoke), whose maintainers describe it as a research prototype rather than a general production optimizer. Do not introduce STOKE as a dependency. [OpenTuner's search-technique tutorial](https://opentuner.org/tutorial/techniques/) provides a useful separation of configuration generation, measurement and selection; the initial Redpilator experiment needs only a small implementation of that pattern.

### 15.4 Required artifacts

Save the search seed, full configuration list, source/program/tool hashes, actual code layout, build costs, raw paired timings, correctness results, training/holdout split and final decision. Retain rejected samples as well as winners. Identify whether a reported gain is versus production, alignment-only or another selected configuration.

Add self-checks for configuration bounds, deterministic proposal generation, duplicate suppression, source-anchor drift, correctness rejection and objective aggregation. Keep timing and correctness instrumentation out of final timed modules. No production change is authorized by a provisional search score alone.
