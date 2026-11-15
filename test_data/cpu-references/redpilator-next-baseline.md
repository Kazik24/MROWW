# Baseline for sequential optimization experiments

Decision: baseline usable with explicit tools, process-owned caches and serial
tests. No production code or frozen expectations changed. The prior dirty
worktree is preserved. Source is HEAD `4ef372fe2da16107cb08573944520c32e943c0f3`
plus the existing digital-batch census; its tracked patch hash and tool binary
hashes are recorded in `redpilator-next-baseline.json`.

Without overrides, the isolated pressure-plate test reproduces LLVM preparation
failure. Unity WebGL Clang 22.0.0git and Rust lld-link 22.1.8 successfully compile
and load the freestanding Windows x64 C23 engine. Neither tool was on PATH.
No DLL override was present. Dot-source `redpilator-next-env.ps1` from the repo
root to reproduce the explicit paths and use an isolated owned cache. The cache
code creates its ownership marker and takes an exclusive root lock; use a
different suffix for each process. No shared/default cache was deleted manually.

Both isolated LLVM preparation and
`executed_plot_reuses_exported_state_and_keeps_pending_ticks_once` pass with this
configuration. The default parallel full filter reports 330 passed, 3 failed,
30 ignored; the failures are the plot activation/cache tests
`executed_plot_reuses_exported_state_and_keeps_pending_ticks_once`,
`executor_cache_refreshes_drained_command_output_queue`, and
`activation_refreshes_output_drained_while_cold_or_cached_compilation_runs`.
All pass in the serial full filter: **333 passed, 0 failed, 30 ignored**.
Thus there is still a concurrency-dependent cold-build limitation; this does not
establish its precise root cause or fix parallel operation. No independent
deterministic plot defect reproduces in isolation/serial operation.

```powershell
. ./test_data/cpu-references/redpilator-next-env.ps1 serial
cargo test -p mchprs_core --lib redpilator --locked -- --test-threads=1
cargo check -p mchprs_core --all-targets --all-features --locked
cargo build --release -p mchprs_core --features plot-bench --example bench_engine_compare --locked
# Only after all builds and tests finish:
. ./test_data/cpu-references/redpilator-next-env.ps1 timing
$env:MCHPRS_ENGINE_KERNEL_ANALYSIS='1'
(Get-Process -Id $PID).ProcessorAffinity = 256
target/release/examples/bench_engine_compare.exe zcore work/redpilator-next/baseline-zcore-native.json 5000 --samples=5
```

All-target/all-feature check passes. The benchmark runs alone on CPU affinity
mask 256, strict LLVM, redstone publication setting with zero publication calls
inside timing, 413 preparation ticks and 5,000 timed ticks per fresh executor.
All five full native snapshots agree, and all five unchanged frozen ZCore screen
and reset assertions pass. Samples and compilation duration are in the JSON.
Peak memory was not collected; compilation occurs once and is not a five-sample
cold-compile measurement. This is a repeatability baseline, not an A/B speedup.
It is separate from the earlier noisy strict Rust measurements. Fixture hashes
remain those in `digital-batch-fixture-hashes.json`.

Logs and generated native artifacts are under ignored `work/redpilator-next/`.
No ignored capture/export test was run.
