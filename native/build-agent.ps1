param([string]$Tag = 'ping1')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if ($Tag -notmatch '^[a-z0-9]+$') { throw 'Invalid agent tag' }
$env:ZIG_GLOBAL_CACHE_DIR = Join-Path (Get-Location).Path '.zig-cache\global'
$env:ZIG_LOCAL_CACHE_DIR = Join-Path (Get-Location).Path '.zig-cache\local'
$taskCompiler = Join-Path (Get-Location).Path '.native-deps\ziglang\zig.exe'
Set-Content -LiteralPath 'native\build_tag.h' -Value ('#define AGENT_TAG L"' + $Tag + '"') -Encoding Ascii
& $taskCompiler cc -x c++ -target x86_64-windows-gnu -shared -O2 -std=c++17 -fno-exceptions -fno-rtti -nostdlib++ native/agent.cpp -o ('native/SekiroParamAgent12-' + $Tag + '.dll') -luser32
if ($LASTEXITCODE -ne 0) { throw 'Native agent build failed' }
