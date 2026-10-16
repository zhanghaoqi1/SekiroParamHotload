$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONPATH = Join-Path $PSScriptRoot '.build-deps'
$taskProfile = Get-Content -LiteralPath 'profile.json' -Raw | ConvertFrom-Json
$taskNativePath = Join-Path $PSScriptRoot $taskProfile.native.dll
if ((Get-FileHash -LiteralPath $taskNativePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskProfile.native.sha256) { throw 'Native DLL fingerprint differs from profile.json' }
$taskCliStage = Join-Path $PSScriptRoot 'build\stage-1.3-cli'
$taskGuiStage = Join-Path $PSScriptRoot 'build\stage-1.3-gui'
$taskDistribution = Join-Path $PSScriptRoot 'dist\SekiroParamHotload-1.3'
foreach ($taskOutputPath in @($taskCliStage, $taskGuiStage, $taskDistribution)) {
    if (-not ([IO.Path]::GetFullPath($taskOutputPath)).StartsWith($PSScriptRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Build target escaped workspace' }
}
python -m PyInstaller --noconfirm --clean --onedir --console --noupx --name SekiroParamHotload --distpath $taskCliStage --workpath 'build\work-1.3-cli' --add-data 'profile.json;.' --add-data 'assets;assets' --icon 'assets/app.ico' --add-binary 'native/SekiroParamAgent12-release2.dll;native' hotload.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
python -m PyInstaller --noconfirm --clean --onedir --windowed --noupx --name SekiroParamHotloadGUI --distpath $taskGuiStage --workpath 'build\work-1.3-gui' --add-data 'profile.json;.' --add-data 'assets;assets' --icon 'assets/app.ico' --add-binary 'native/SekiroParamAgent12-release2.dll;native' gui.py
if ($LASTEXITCODE -ne 0) { throw 'GUI build failed' }
New-Item -ItemType Directory -Force -Path $taskDistribution | Out-Null
# Merge build outputs while preserving existing state/journals in the distribution.
Get-ChildItem -LiteralPath (Join-Path $taskGuiStage 'SekiroParamHotloadGUI') | Copy-Item -Destination $taskDistribution -Recurse -Force
Copy-Item -LiteralPath (Join-Path $taskCliStage 'SekiroParamHotload\SekiroParamHotload.exe') -Destination $taskDistribution -Force
foreach ($taskDoc in @('README.md', 'STRUCTURAL_RELOAD.md', 'THIRD_PARTY.md', 'VALIDATION.md')) { Copy-Item -LiteralPath $taskDoc -Destination $taskDistribution -Force }
Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.cmd' | Copy-Item -Destination $taskDistribution -Force
$taskLicenseDir = Join-Path $taskDistribution 'licenses'
New-Item -ItemType Directory -Force -Path $taskLicenseDir | Out-Null
Copy-Item -LiteralPath 'reference\SoulsFormats-LICENSE' -Destination $taskLicenseDir
Copy-Item -LiteralPath 'reference\DSMapStudio-LICENSE' -Destination $taskLicenseDir
Copy-Item -LiteralPath '.native-deps\ziglang\LICENSE' -Destination (Join-Path $taskLicenseDir 'Zig-LICENSE')
Copy-Item -LiteralPath '.native-deps\ziglang\lib\libc\mingw\COPYING' -Destination (Join-Path $taskLicenseDir 'MinGW-COPYING')
$taskPythonPath = python -c "import sys; print(sys.base_prefix)"
Copy-Item -LiteralPath (Join-Path $taskPythonPath 'LICENSE.txt') -Destination (Join-Path $taskLicenseDir 'Python-LICENSE.txt')
Get-ChildItem -LiteralPath '.build-deps' -Recurse -File -Filter 'COPYING*' | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $taskLicenseDir ('PyInstaller-' + $_.Name)) }
foreach ($taskTkPackage in @('tcl8.6', 'tk8.6')) {
    $taskTkLicense = Join-Path $taskPythonPath ('tcl\' + $taskTkPackage + '\license.terms')
    if (Test-Path -LiteralPath $taskTkLicense) { Copy-Item -LiteralPath $taskTkLicense -Destination (Join-Path $taskLicenseDir ($taskTkPackage + '-license.terms')) }
}
