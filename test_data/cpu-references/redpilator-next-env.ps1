# Dot-source from the repository root; each concurrent process needs its own cache suffix.
param([string]$Cache = 'baseline')
$env:MCHPRS_LLVM_CLANG = 'C:/Program Files/Unity/Hub/Editor/6000.6.0f1/Editor/Data/PlaybackEngines/WebGLSupport/BuildTools/Emscripten/llvm/clang.exe'
$env:MCHPRS_LLVM_LINKER = 'C:/Users/Maciej/.rustup/toolchains/stable-x86_64-pc-windows-msvc/lib/rustlib/x86_64-pc-windows-msvc/bin/gcc-ld/lld-link.exe'
foreach ($tool in @($env:MCHPRS_LLVM_CLANG, $env:MCHPRS_LLVM_LINKER)) {
    if (!(Test-Path -LiteralPath $tool -PathType Leaf)) { throw "Missing tool: $tool" }
}
Remove-Item Env:MCHPRS_LLVM_DLL -ErrorAction SilentlyContinue
$env:MCHPRS_LLVM_WORK_DIR = Join-Path (Get-Location) "work/redpilator-next/llvm-$Cache"
