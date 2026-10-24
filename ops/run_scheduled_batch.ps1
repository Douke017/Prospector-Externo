# HU-16: invocacion programada opt-in desde UN SOLO host Windows.
# No ejecutar manualmente sin aprobacion de la fuente: -DryRun no hace crawling.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')]
    [string]$SourceId,
    [switch]$DryRun,
    [ValidateRange(0, 30)]
    [int]$HoldLockSeconds = 0
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$logDir = Join-Path $root 'logs\scheduler'
$lockPath = Join-Path $logDir 'batch.lock'
$lockStream = $null
$transcriptStarted = $false
$oldLocation = (Get-Location).Path
$exitCode = 1

try {
    [void](New-Item -ItemType Directory -Path $logDir -Force)
    # FileShare.None aplica entre TODOS los procesos que usan esta carpeta
    # (incluso sesiones de Windows diferentes). SO libera al morir el proceso.
    # Bloqueo conservador GLOBAL al batch, mas estricto que solo misma fuente.
    try {
        $lockStream = [System.IO.File]::Open(
            $lockPath,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
    }
    catch [System.IO.IOException] {
        Write-Output 'HU16_ALREADY_RUNNING: se omite la corrida programada; bloqueo del batch ocupado.'
        exit 0
    }

    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
    $logPath = Join-Path $logDir ("batch_${SourceId}_${stamp}_$PID.log")
    Start-Transcript -LiteralPath $logPath -ErrorAction Stop | Out-Null
    $transcriptStarted = $true
    Set-Location -LiteralPath $root
    Write-Output "HU16_LOCK_ACQUIRED: $lockPath"
    Write-Output "HU16_SOURCE: $SourceId"
    Write-Output "HU16_LOG: $logPath"

    if ($DryRun) {
        Write-Output 'HU16_DRY_RUN: docker compose up -d --wait --wait-timeout 120 mongo api'
        Write-Output "HU16_DRY_RUN: docker compose run --rm --no-deps batch --source $SourceId"
        if ($HoldLockSeconds -gt 0) {
            Start-Sleep -Seconds $HoldLockSeconds
        }
        $exitCode = 0
    }
    else {
        & docker compose config --quiet
        if ($LASTEXITCODE -ne 0) { throw "docker compose config fallo: $LASTEXITCODE" }
        & docker compose up -d --wait --wait-timeout 120 mongo api
        if ($LASTEXITCODE -ne 0) { throw "docker compose up fallo: $LASTEXITCODE" }
        & docker compose run --rm --no-deps batch --source $SourceId
        $exitCode = $LASTEXITCODE
        Write-Output "HU16_BATCH_EXIT_CODE: $exitCode"
        # No transformar los codigos 2/3 del crawler a 0.
    }
}
catch {
    Write-Output "HU16_ERROR: $($_.Exception.Message)"
    $exitCode = 1
}
finally {
    Set-Location -LiteralPath $oldLocation
    if ($transcriptStarted) {
        try { Stop-Transcript | Out-Null } catch { Write-Output "HU16_TRANSCRIPT_ERROR: $($_.Exception.Message)" }
    }
    if ($null -ne $lockStream) { $lockStream.Dispose() }
}
exit $exitCode
