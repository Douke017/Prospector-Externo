# Instala un ejemplo de disparador DIARIO en Task Scheduler (opt-in).
# El equipo debe aprobar antes la Decisión 1.2: scheduler tecnico y cadencia.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')]
    [string]$SourceId,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')]
    [string]$DailyAt,
    [switch]$Register
)
$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'run_scheduled_batch.ps1'
if (-not (Test-Path -LiteralPath $scriptPath)) { throw 'run_scheduled_batch.ps1 no encontrado' }
$taskName = "ProspectorExterno-$SourceId"
$argument = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $scriptPath + '" -SourceId ' + $SourceId
Write-Output "HU16_TASK: $taskName"
Write-Output "HU16_TRIGGER: DIARIO A LAS $DailyAt (hora local del host)"
Write-Output "HU16_ARGUMENTS: $argument"
if (-not $Register) {
    Write-Output 'HU16_PREVIEW_ONLY: no se registro ni habilito ninguna tarea.'
    return
}
# Requiere permisos de registro de tareas para el usuario actual; no elevar por defecto.
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argument -WorkingDirectory (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 8)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description 'DATAX Prospector Externo: batch oportunista. Revisar logs y codigo de salida.' -Force | Out-Null
# El registro queda DESHABILITADO. La habilitacion requiere accion expresa.
Disable-ScheduledTask -TaskName $taskName | Out-Null
Write-Output "HU16_REGISTERED_DISABLED: $taskName"
Write-Output "HU16_ENABLE_MANUALLY: Enable-ScheduledTask -TaskName '$taskName'"
