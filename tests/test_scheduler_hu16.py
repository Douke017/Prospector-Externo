"""Contratos de scheduling opt-in. En Linux NO ejecutan PowerShell/Task Scheduler."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RUN=(ROOT/'ops/run_scheduled_batch.ps1').read_text(encoding='utf-8')
REG=(ROOT/'ops/register_windows_scheduler.ps1').read_text(encoding='utf-8')
README=(ROOT/'README.md').read_text(encoding='utf-8')


def test_batch_lock_covers_docker_and_is_released():
    assert '[System.IO.FileShare]::None' in RUN
    assert 'HU16_ALREADY_RUNNING' in RUN
    assert 'docker compose run --rm --no-deps batch --source' in RUN
    assert '$lockStream.Dispose()' in RUN
    assert RUN.index('FileShare]::None') < RUN.index('docker compose up') < RUN.index('$lockStream.Dispose()')

def test_scheduler_does_not_autorun_or_silence_partial_errors():
    assert 'DryRun' in RUN
    assert 'exit $exitCode' in RUN
    assert '$exitCode = $LASTEXITCODE' in RUN
    assert 'Start-Transcript' in RUN
    assert 'docker compose down' not in RUN

def test_registration_explicit_disabled_and_single_instance():
    assert 'switch]$Register' in REG
    assert 'if (-not $Register)' in REG
    assert 'MultipleInstances IgnoreNew' in REG
    assert 'Disable-ScheduledTask' in REG
    assert 'New-ScheduledTaskTrigger -Daily -At $DailyAt' in REG

def test_scheduler_documented_as_opt_in_and_local_only():
    assert 'Decisión 1.2' in README
    assert 'HU-16' in README
    assert 'multinodo' in README
    assert 'logs/' in (ROOT/'.dockerignore').read_text(encoding='utf-8')
    assert 'logs/scheduler/' in (ROOT/'.gitignore').read_text(encoding='utf-8')
