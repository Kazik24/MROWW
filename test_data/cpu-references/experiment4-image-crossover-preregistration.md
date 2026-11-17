# Image-null crossover: frozen measurement and analysis plan

Declared before the first crossover timing session. The exact schedules,
artifact hash, helper RVAs, fixture hashes and conditions are in
`experiment4-image-crossover-plan.json`. No alignment search is authorized here.

## Design and bounds

Two independent fresh processes, each with fresh independent byte-identical
DLL master copies A/B (including PE timestamp). Each process compiles the same
strict PM1 seed once, then executes 20 temporal blocks. Each block contains
one separate-image pair and one same-image A/A pair: **20 pairs per comparison
per session**, 80 pairs total. No builds, tests, profiles or other census run
alongside timing. No extra pairs are added merely because a result disappoints.

A seeded order-20 Paley/Hadamard design supplies balanced, pairwise orthogonal
binary factors. Seeds are 2026111608 and 2026111609, with independent shuffled
row order. Factors are:

- File/path mapping: separate A/B master identities swap between fixed alias
  slots. For A/A this selects which physical master backs both labels.
- Module load order, measured-executor allocation order, measured execution
  order and warmup execution order are independently balanced.
- Path set: separate-image pairs use either of two equal-length alias-directory
  sets. A/A uses the same absolute slot-zero path for both labels in that set.
- Which comparison runs first within each temporal block is also balanced.

Hardlinks swap file identity/path assignment without changing any image byte.
Master files are independent copies, not hardlinks to each other. The runner
records Win32 volume/file IDs, confirms alias assignment, and removes only its
own two aliases after all image/executor references have been released.

## Lifecycle, timing and correctness

One owning load reference per label is acquired in the prescribed load order
before constructing any executor. Both references remain live through the
pair. A/A must have one shared image base; separate copies must have distinct
bases. The executor's actual `ne_tick` address must equal its pinned image base
plus the linked RVA. This catches accidental loader coalescing or another DLL.

Each label receives one fresh full-protocol warmup with the same initialization
as its measured run. Warmup executors are allocated in the allocation order,
executed/validated in warmup order, then released. Both measured executors are
created before timing either. Initialization retains the original native
compiler options and shared physical program; the existing native restart's
temporary default native instance is still constructed and released identically
for both labels. No runtime execution or production alignment is modified.

The two full 12,070-tick intervals run adjacently in the prescribed execution
order. Only Start and scalar clock/CPU capture occur outside each interval;
there is no JSON construction, allocation, snapshot, reset, publication or
validation between intervals. Both are validated afterwards. Complete native
snapshots must match within/across comparisons; frozen outputs and all ordered
messages must match. Reset must be quiet, including two interpreted continuation
ticks. Unlimited command output uses the existing fixed-zero clock hook, with
all logical counters/effects retained in snapshot equality.

High process priority and affinity mask 256 are checked inside each process.
IO publication is configured, with no timed publication/client calls. Loaded
paths, actual bases/helper addresses, tick/state addresses, arena sizes, file
IDs/hashes, load/allocation/execution orders and process-relative timestamps are
captured inside these processes outside timing. Raw elapsed nanoseconds are
retained. Module bases for helpers come from the actual pinned handles and
validated linked RVAs, not a separate probe process.

## Analysis and decision

For each pair report B/A throughput = elapsed(A)/elapsed(B), and its log.
The primary estimate is the exponentiated mean log ratio. Report the median
and every raw paired ratio too. Analyze each session/comparison separately;
never subtract a null gain from candidate results.

Uncertainty uses **10,000 circular moving-block bootstrap draws**, with primary
block length four temporal blocks and sensitivity lengths two and five. A
temporal block contains both comparison pairs; sampling preserves their local
position in time rather than treating ticks, halves or pairs as independent.
The reported 95% interval is the envelope of the three percentile intervals.
This is conservative block-length sensitivity, not an IID confidence interval.
It covers only the observed session; drift on longer timescales remains a limit.

Order diagnostics fit log ratio to all seven signed design factors and a
centered linear block-time covariate. Report factor contrasts (twice the OLS
coefficient) and start-to-end linear trend. Resample contiguous temporal blocks
and refit the model with original time coordinates for confidence intervals.
Singular bootstrap designs are retried and counted, up to 30,000 draws for
10,000 valid fits. No measured observation is excluded by this numerical rule.

Calibration passes only if **both same-image and separate-image 95% interval
envelopes lie entirely inside [0.99, 1.01] in both sessions**, with no factor
(or time trend) whose interval excludes zero with the same sign in both
sessions. A failure to detect an order effect is not proof that it is absent;
the interval gate is mandatory. Twenty pairs may be insufficient, in which
case the outcome is inconclusive.

All timing outliers are retained. A correctness, identity, address or schedule
failure invalidates a whole session and is reported explicitly. A concrete
harness defect permits one documented premeasurement amendment and repetition
of both sessions; otherwise stop and document unresolved effects. No alignment
mutations, finalists or production policy changes follow this calibration.
