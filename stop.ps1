param([int]$Port = 8766)
$ErrorActionPreference = 'Stop'
$recordPath = Join-Path $PSScriptRoot ('.runtime\process-' + $Port + '.json')
if (-not (Test-Path -LiteralPath $recordPath)) { Write-Output 'No owned process record found.'; exit 0 }
$record = Get-Content -LiteralPath $recordPath -Encoding UTF8 | ConvertFrom-Json
$processInfo = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$record.pid)
if (-not $processInfo) { Write-Output 'Already stopped.'; exit 0 }
$expectedServer = Join-Path $PSScriptRoot 'server.py'
if ($record.server -ne $expectedServer -or $processInfo.CommandLine -notlike ('*' + $expectedServer + '*')) {
    throw 'Process identity does not match this application. Nothing was stopped.'
}
$process = Get-Process -Id $record.pid
$recordedStart = if ($record.started -is [datetime]) { $record.started.ToUniversalTime() } else { [datetime]::Parse($record.started, [cultureinfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime() }
if ([Math]::Abs(($process.StartTime.ToUniversalTime() - $recordedStart).TotalSeconds) -gt 2) {
    throw 'Process start time changed. Nothing was stopped.'
}
Stop-Process -Id $record.pid
Write-Output 'Xiaohai control stopped. In-memory credentials are cleared.'
