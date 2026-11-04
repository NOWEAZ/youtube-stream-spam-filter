param([switch]$DemoData, [switch]$Solar, [string]$Blocklist = '', [int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$serverArgs = @('-m', 'backend.server', '--port', "$Port")
if ($DemoData) { $serverArgs += '--demo-data' }
if ($Solar) { $serverArgs += '--solar' }
if ($Blocklist) { $serverArgs += @('--blocklist', $Blocklist) }
$bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (Test-Path -LiteralPath $bundledPython) {
  & $bundledPython @serverArgs
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
  & py -3 @serverArgs
} else { & python @serverArgs }
