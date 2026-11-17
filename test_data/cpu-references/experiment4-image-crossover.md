# Separate-image timing crossover — stopped at user request

The run was stopped before analysis at the user's request. Production execution
and alignment are unchanged. Existing worktree changes are preserved.

The sealed design is in `experiment4-image-crossover-plan.json` and
`experiment4-image-crossover-preregistration.md`; source/runner hashes are in
`experiment4-image-crossover-seal.json`. No schedule or analysis amendments ran.

| Session | Separate-image pairs | Same-image A/A pairs | Status |
| --- | ---: | ---: | --- |
| 1 | 20 | 20 | Complete |
| 2 | 18 | 18 | Interrupted |

All 76 saved pairs passed complete native snapshot equality, frozen PM1 output
and ordered-command checks, quiet reset and two interpreted continuation ticks.
All saved separate-image pairs used distinct module bases and independent file
identities; A/A pairs shared one base. Actual executor tick pointers matched the
pinned images. Each measured pair had identically initialized fresh executors
and one complete warmup per label. Both measured executors were allocated before
either interval; validation/JSON construction occurred only after both intervals.

The previous runner coupled module loading/allocation to execution order and
performed snapshot, reset, output and next-executor setup between timed halves.
The crossover independently balances these orders, file-to-path assignment,
warmup order and temporal placement, with byte-identical master copies including
their timestamp. This removes those design couplings; it does not yet establish
which caused the earlier apparent gain.

Loaded paths, file IDs, hashes, module bases, hot-helper addresses, native arena
addresses/sizes, orders and raw elapsed nanoseconds are saved in
`experiment4-image-crossover-session0-raw.json.gz` and
`experiment4-image-crossover-session1-raw.json.gz`. Commands and interruption
status are in `experiment4-image-crossover-status.json`.

**No confidence interval or calibration decision is claimed.** The declared
criterion requires both complete sessions and temporal block-bootstrap 95%
intervals within ±1%, with no reproducible modeled identity/order bias. The
second session is incomplete. No outliers were removed, no null gain was
subtracted, and no alignment mutation/finalist was evaluated.

The new harness compiled with the existing `plot-bench` example, passed the
all-target/all-feature core check, and its three schedule/analysis self-checks
passed. The only core additions are benchmark-gated address accessors. Native
C, compiler flags, generated circuit tables and production alignment were not
changed.

## Reproduction after cache cleanup

Generated native caches and binary artifacts under `work` were removed at the
user's request. Old absolute artifact paths in manifests/plans are historical;
rebuild a strict PM1 seed and baseline manifest using the reproduction steps in
`experiment4-alignment-search.md`, then use fresh root/output names:

```powershell
py -3 tools/experiment_image_crossover.py selftest
# MANIFEST must describe the newly rebuilt strict PM1 production DLL and link map.
py -3 tools/experiment_image_crossover.py plan MANIFEST work/crossover-fresh PLAN.json
# Finish all builds/tests first; explicit LLVM environment is required.
. ./test_data/cpu-references/redpilator-next-env.ps1 crossover-fresh
py -3 tools/experiment_image_crossover.py run PLAN.json
py -3 tools/experiment_image_crossover.py analyze PLAN.json RESULTS.json
```

Do not reuse the incomplete second session as a complete predeclared session.
No further measurements or analysis were run after the stop request.
