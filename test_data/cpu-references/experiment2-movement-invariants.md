# Experiment 2: bounded movement invariant specialization

**Reject the initial bounded rule families.** No shared-cell exclusion applies
inside the analyzed groups, and constant connections already emit no movement
flips. No invariant, replacement table or runtime specialization was retained.
This is not a claim that every real circuit state is reachable or that larger
coupled-piston invariants cannot exist.

`experiment2-movement-invariants.json` contains strict and relaxed counts,
unknown coverage, compile durations, command, source base, exact variant scope
and resource observation. Fixture hashes and tools are the verified files used
by Experiment 1 and `redpilator-next-baseline.json`. The experiment began only
after Experiment 1's reject decision was recorded.

The bounded family checks groups of 2–4 physical token axes with at most 64
physical combinations, including each token's absent state. It enumerates at
most 1,000,000 physical rows per program and records out-of-scope or exhausted
groups as unknown. Work per enumerated row is bounded by four token positions
and their pair comparisons; scratch occupancy has four slots. Physical rows
are counted before electrical digit aliasing, so they are not automatically
removable electrical table rows. Both modes have zero collision rows in every
analyzed group. Single-axis groups cannot provide a cross-token exclusion.

The conservative transition model allows each token to depart to absent, then
arrive at any validated legal cell, independently. This overapproximates all
external inputs, scheduled piston decisions and intermediate motion states;
it does not assume a trace proves an action impossible. The initial physical
digits are valid by existing program initialization/validation. Departure and
arrival preserve that domain by construction. Every Cartesian assignment is
reachable in this overapproximation by returning axes to absent and placing
them individually. Thus this model alone proves no additional unreachable
rows. A physical shared-cell exclusion could refine it only with an additional
initial-state and transition-preservation proof; there are no such collision
rows in the bounded census to justify building that proof machinery.

Existing equivalence folding already merges digits whose connection membership
agrees for every other axis assignment. The movement lowering compares each
membership row against that axis's absent row and emits a flip only when they
differ (`movement.rs::movement_group`). A connection constant over all rows
therefore has **zero movement flips**, irrespective of dynamic execution count.
The census does find constant connections; their presence is not new removable
movement work. Source-output contribution updates can still use those links;
their frequency and specialization were not measured or claimed here. Table
storage reduction alone cannot pass the throughput gate.

Unknown/larger groups remain canonical. No whole-CPU search, solver or unproved
cross-axis assumption was introduced. Consequently there is no affected dynamic
movement set, no proven table reduction, and no useful enabled/disabled kernel
pair to time. Paired samples are empty for this reason, rather than fabricated
zero timings. New production metadata and native artifact deltas are zero.
No speedup, universal reachability proof or new Rust/native equivalence is
claimed.

Focused tests cover shared versus disjoint cells, absent states, axis/row caps
and budget exhaustion. They pass, as does the all-target/all-feature check.
The final serial regression result is recorded in the accompanying JSON.
Movement ordering, physical token identities, callbacks, logical costs, failure
prefixes, publication, reset and interpreter handoff use the original code.
No ABI/version change is needed. No ignored capture/export test or additional
long candidate replay was run because there is no candidate execution path.

Final shared serial regression: **335 passed, 0 failed, 30 ignored**.
Across the seven relaxed fixtures, 40,123 bounded multi-axis groups and 628,727
physical combinations were examined; 5,591 larger groups remain unknown.
