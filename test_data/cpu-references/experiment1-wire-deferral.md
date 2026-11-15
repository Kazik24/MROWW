# Experiment 1: per-wire deferred state

**Reject: zero additional candidates in all seven CPU families.** Both modes
were compiled; strict mode is outside the proposed change. In relaxed mode every
wire already satisfies the native circuit-wide eligibility condition:

| Fixture | Wires already covered | Additional wires |
| --- | ---: | ---: |
| ANPU | 34,781 | 0 |
| BubbleSort | 41,354 | 0 |
| ZCore | 43,454 | 0 |
| Current PM1 protocol fixture | 168,537 | 0 |
| PC_COUNTER | 1,890 | 0 |
| FPU | 25,061 | 0 |
| CHUNGUS | 133,623 | 0 |

Legacy PM1_SORT still rejects compilation with moving piston state; it is not
counted as zero. Full counts, compile durations, command, variant description and
a process memory observation are in `experiment1-wire-deferral.json`. The memory
observation is an in-flight high-water mark, not a measured incremental cost.
Fixture hashes were rechecked against `digital-batch-fixture-hashes.json`.
Source base and toolchain are in `redpilator-next-baseline.json`.

`generated.rs::with_tick_capacity` and `engine.c::can_defer_wire_power` require
relaxed mode, at least one wire, and no outgoing fixed, arrangement or side-input
links for **every** wire. The diagnostic reproduces that condition exactly for
the already-covered count. Only if it fails does it count additional wires with
those three empty outgoing indexes, no watched-wire entry, reactive flag/source/
neighbour dependencies, source watchers, comparator override consumers or output
operations. No eligibility condition of the rejected digital-batch rule changed.

Consumer audit: Rust movement/output callbacks use `refresh_wire_power`, which
stores strength and propagates outgoing contributions; watched and reactive
paths have separate strength caches. Rust publication projects stored blocks.
Native `refresh_wire` skips storage under the existing whole-circuit condition;
`represented_state` derives wire strength for output, selected reads, visual
publication and full snapshots. Movement graph replay has its own classified
immutable inputs and materialization fallback. Native projection/reset consumes
the full snapshot and preserves contributions, pending ticks and piston state.
These paths and strict behavior are unchanged.

The existing wire tests establish that stored strength can differ from current
input strength between refresh boundaries (including initial state and operation
limit prefixes). Unwatched status alone therefore cannot justify replacing
history with present strength in a mixed circuit. No new correctness equivalence
is claimed. The baseline serial suite passed all existing wire history,
publication, snapshot, pending-work and failure-prefix tests.

The additional set is empty, so its dynamic update frequency and potentially
avoided work are **exactly zero**, independent of protocol inputs. Tick counters,
new eligibility metadata, reconstruction kernels and an A/B variant would add
no useful evidence. No paired performance run or long candidate replay was
performed, and no speedup is claimed. New production metadata/artifact cost is
zero. The focused classification test and all-target/all-feature check pass.
Only compile-time diagnostics remain, gated by the existing phase-timing flag;
there is no ticking or publication change.

This decision was completed before starting Experiment 2.
Final shared serial regression with both census helpers: **335 passed, 0 failed,
30 ignored**; all-target/all-feature check passes.
