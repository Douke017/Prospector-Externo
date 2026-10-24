"""Pruebas unitarias para HealthStateMachine y CadenceState."""

import pytest
from datetime import datetime, timedelta, timezone
from prospector_externo.domain.health import HealthStateMachine, HealthStatus
from prospector_externo.domain.cadence import CadenceState, UpdateCategory


def test_health_state_machine_success_and_failure():
    sm = HealthStateMachine(failure_threshold=3)
    assert sm.current_status == HealthStatus.ACTIVE
    assert sm.can_crawl() is True

    # Primer fallo -> WARNING
    s1 = sm.record_failure("TIMEOUT")
    assert s1 == HealthStatus.WARNING
    assert sm.can_crawl() is True

    # Segundo fallo -> WARNING
    s2 = sm.record_failure("TIMEOUT")
    assert s2 == HealthStatus.WARNING

    # Tercer fallo -> SUSPENDED
    s3 = sm.record_failure("CONNECTION_ERROR")
    assert s3 == HealthStatus.SUSPENDED
    assert sm.can_crawl() is False

    # Recuperación exitosa
    sm.record_success()
    assert sm.current_status == HealthStatus.ACTIVE
    assert sm.consecutive_failures == 0
    assert sm.can_crawl() is True


def test_cadence_conservative_adaptation():
    cadence = CadenceState(category=UpdateCategory.DAILY)
    initial = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # Las cinco observaciones DAILY deben ser suficientes, ni cuatro ni siete.
    for i in range(4):
        cadence.register_no_change(now=initial + timedelta(days=i))
    assert cadence.category == UpdateCategory.DAILY
    cadence.register_no_change(now=initial + timedelta(days=4))
    assert cadence.category == UpdateCategory.WEEKLY
    assert cadence.consecutive_no_change_count == 5
    assert cadence.weekly_no_change_count == 0

    # Cuatro llamadas realizadas inmediatamente no equivalen a cuatro semanas.
    for i in range(4):
        cadence.register_no_change(now=initial + timedelta(days=4, hours=i + 1))
    assert cadence.category == UpdateCategory.WEEKLY

    # Tras cuatro semanas de observaciones semanales, sí pasa a MONTHLY.
    cadence.register_no_change(now=initial + timedelta(days=32))
    assert cadence.category == UpdateCategory.MONTHLY
    assert cadence.next_eligible_at == initial + timedelta(days=62)

    cadence.register_change(base_category=UpdateCategory.DAILY, now=initial + timedelta(days=33))
    assert cadence.category == UpdateCategory.DAILY
    assert cadence.consecutive_no_change_count == 0
    assert cadence.weekly_no_change_count == 0
    assert cadence.weekly_no_change_since_at is None


def test_native_weekly_cadence_requires_elapsed_weeks():
    cadence = CadenceState(category=UpdateCategory.WEEKLY)
    t0 = datetime(2026, 2, 1, tzinfo=timezone.utc)
    for i in range(4):
        cadence.register_no_change(now=t0 + timedelta(weeks=i))
    assert cadence.category == UpdateCategory.WEEKLY
    cadence.register_no_change(now=t0 + timedelta(weeks=4))
    assert cadence.category == UpdateCategory.MONTHLY
