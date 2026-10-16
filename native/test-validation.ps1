$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$env:ZIG_GLOBAL_CACHE_DIR = Join-Path (Get-Location).Path '.zig-cache\global'
$env:ZIG_LOCAL_CACHE_DIR = Join-Path (Get-Location).Path '.zig-cache\local'
New-Item -ItemType Directory -Force -Path 'build\native-tests' | Out-Null
& '.\.native-deps\ziglang\zig.exe' cc -x c++ -target x86_64-windows-gnu -O2 -std=c++17 -fno-exceptions -fno-rtti -nostdlib++ native/validation_test.cpp -o build/native-tests/validation_test.exe -luser32
if ($LASTEXITCODE -ne 0) { throw 'Native validator test build failed' }
& '.\build\native-tests\validation_test.exe'
if ($LASTEXITCODE -ne 0) { throw 'Native validator regression failed' }
