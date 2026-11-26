# Builds assets\openxr\aether_openxr_layer.dll using Visual Studio 2022 Build Tools (x64)
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$outDir = Join-Path $repoRoot "assets\openxr"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) {
    throw "vswhere.exe not found. Please install Visual Studio 2022 Build Tools (C++)."
}
$vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vsPath) {
    throw "Visual Studio C++ x64 Build Tools not found."
}
$vcvars = Join-Path $vsPath "VC\Auxiliary\Build\vcvars64.bat"
$src = Join-Path $PSScriptRoot "aether_openxr_layer.cpp"
$outDll = Join-Path $outDir "aether_openxr_layer.dll"
$objFile = Join-Path $env:TEMP "aether_openxr_layer.obj"

$cmd = "`"$vcvars`" && cl /nologo /O2 /MT /LD /EHsc /std:c++17 /Fo:`"$objFile`" `"$src`" /Fe:`"$outDll`" /link d3d11.lib"
cmd.exe /c $cmd
if ($LASTEXITCODE -ne 0) {
    throw "MSVC build failed with exit code $LASTEXITCODE"
}
# Clean up .exp/.lib build artifacts from assets\openxr
Remove-Item -ErrorAction SilentlyContinue (Join-Path $outDir "aether_openxr_layer.exp"), (Join-Path $outDir "aether_openxr_layer.lib"), $objFile
Write-Host "Built $outDll successfully."
