# Full unchanged strict PM1 protocol; calibrates measurement error before ranking alignments.
param(
    [Parameter(Mandatory)][string]$Manifest,
    [Parameter(Mandatory)][string]$OutputPrefix,
    [ValidateSet('same','separate','separate-high')][string]$Mode='separate'
)
$ErrorActionPreference='Stop'
. ./test_data/cpu-references/redpilator-next-env.ps1 "alignment-calibrate-$PID"
$artifacts=(Get-Content -Raw -LiteralPath $Manifest | ConvertFrom-Json).artifacts
if ($artifacts.production.text_sha256 -ne $artifacts.flip64.text_sha256) { throw 'This calibration requires a no-effect flip64 control' }
$paths=@{production=(Join-Path $artifacts.production.directory 'engine.dll'); flip64=(Join-Path $artifacts.flip64.directory 'engine.dll')}
if ($Mode -eq 'same') { $paths.flip64=$paths.production }
$labels=@()
if ($Mode -eq 'separate') { $labels=@('production','flip64','production') }
if ($Mode -eq 'separate-high') { $labels=@('production','flip64','flip64','production') }
$warmup=$labels.Count
for ($pair=0; $pair -lt 5; $pair++) {
    if ($pair % 2 -eq 0) { $labels+=@('production','flip64') } else { $labels+=@('flip64','production') }
}
$prefix=[IO.Path]::GetFullPath($OutputPrefix)
if (Test-Path -LiteralPath "$prefix-results.json") { throw 'Use a fresh output prefix' }
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($prefix)) | Out-Null
$modules=@($labels | ForEach-Object { $paths[$_] })
ConvertTo-Json -InputObject $modules | Set-Content -LiteralPath "$prefix.modules.json"
$env:MCHPRS_ENGINE_KERNEL_ANALYSIS='1'
$env:MCHPRS_REDPILATOR_COMPILE_TIMEOUT_SECS='1200'
(Get-Process -Id $PID).ProcessorAffinity=256
$arguments=@('test_data/cpu-references/pm1_redpilator_protocol.json',"$prefix-results.json",'llvm','12070','1',
    "--modules=$prefix.modules.json",'--publication=io','--reference=test_data/cpu-references/pm1_redpilator_reference.json')
$info=[Diagnostics.ProcessStartInfo]::new((Resolve-Path target/release/examples/bench_pm1_compare.exe).Path)
$info.UseShellExecute=$false
$info.CreateNoWindow=$true
$info.RedirectStandardOutput=$true
$info.RedirectStandardError=$true
foreach ($argument in $arguments) { $info.ArgumentList.Add($argument) }
$process=[Diagnostics.Process]::Start($info)
$process.ProcessorAffinity=256
if ($Mode -eq 'separate-high') { $process.PriorityClass='High' }
$stdout=$process.StandardOutput.ReadToEndAsync()
$stderr=$process.StandardError.ReadToEndAsync()
@{Command=@($info.FileName)+$arguments; Mode=$Mode; Affinity=256; Priority=$process.PriorityClass.ToString();
    WarmupExecutions=$warmup; Labels=$labels; StartedUtc=[DateTime]::UtcNow.ToString('o')} |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$prefix-command.json"
$process.WaitForExit()
[IO.File]::WriteAllText("$prefix-run.log",$stdout.GetAwaiter().GetResult())
[IO.File]::WriteAllText("$prefix-error.log",$stderr.GetAwaiter().GetResult())
if ($process.ExitCode -ne 0) { throw "Calibration failed: see $prefix-error.log" }
$samples=(Get-Content -Raw -LiteralPath "$prefix-results.json" | ConvertFrom-Json).samples
if ($samples.Count -ne $modules.Count -or @($samples.native_state_sha256 | Sort-Object -Unique).Count -ne 1 -or
    !$samples[0].native_state_sha256 -or $samples.reference_matched -contains $false -or $samples.quiet -contains $false) {
    throw 'Incomplete calibration or correctness failure'
}
$pairs=@()
for ($pair=0; $pair -lt 5; $pair++) {
    $first=$warmup+2*$pair
    $baseline=$first+($pair % 2)
    $candidate=$first+(1-($pair % 2))
    $pairs+=@{Pair=$pair; BaselineSample=$baseline+1; ControlSample=$candidate+1; Ratio=$samples[$candidate].tps/$samples[$baseline].tps}
}
$sorted=@($pairs.Ratio | Sort-Object)
$summary=@{Mode=$Mode; Pairs=$pairs; MedianRatio=$sorted[2]; MinRatio=$sorted[0]; MaxRatio=$sorted[4]; Correctness='passed'}
$summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath "$prefix-summary.json"
$summary | ConvertTo-Json -Depth 6
