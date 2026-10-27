param([int]$Port = 8766, [switch]$OpenBrowser)
$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$baseUrl = 'http://127.0.0.1:' + $Port
$running = $null
try { $running = Invoke-RestMethod -Uri ($baseUrl + '/api/health') -TimeoutSec 2 } catch {}
if ($running -and $running.app -eq 'xiaohai-local-control') {
    Write-Output ('Already running: ' + $baseUrl)
    if ($OpenBrowser) { Start-Process $baseUrl }
    exit 0
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw ('Port is already in use. Try: .\start.ps1 -Port ' + ($Port + 1))
}
$venvPython = Join-Path $projectDir '.venv\Scripts\python.exe'
$pythonExe = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { (Get-Command python -ErrorAction Stop).Source }
& $pythonExe -c 'import aiohttp'
if ($LASTEXITCODE -ne 0) { throw 'Missing aiohttp. Create a local .venv and install requirements.txt. See README.md.' }
$runtimeDir = Join-Path $projectDir '.runtime'
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$serverPath = Join-Path $projectDir 'server.py'
$process = Start-Process -FilePath $pythonExe -ArgumentList @('-u', ('"' + $serverPath + '"'), '--port', $Port) -WorkingDirectory $projectDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDir 'server.log') -RedirectStandardError (Join-Path $runtimeDir 'server-error.log')
$record = @{ pid = $process.Id; port = $Port; server = $serverPath; started = $process.StartTime.ToUniversalTime().ToString('o') } | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $runtimeDir ('process-' + $Port + '.json')), $record, [Text.UTF8Encoding]::new($false))
for ($attempt = 0; $attempt -lt 25; $attempt++) {
    Start-Sleep -Milliseconds 200
    try { $health = Invoke-RestMethod -Uri ($baseUrl + '/api/health') -TimeoutSec 1 } catch { $health = $null }
    if ($health -and $health.app -eq 'xiaohai-local-control') {
        Write-Output ('Ready: ' + $baseUrl)
        if ($OpenBrowser) { Start-Process $baseUrl }
        exit 0
    }
    if ($process.HasExited) { break }
}
throw 'Startup failed. Inspect .runtime/server-error.log.'
