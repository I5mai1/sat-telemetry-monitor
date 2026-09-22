"""Unit tests for OOL threshold evaluation in alarms.py."""

from alarms import (
    Severity,
    check_battery_voltage,
    check_bus_temperature,
    check_wheel_speed,
    evaluate_frame,
)

TIMESTAMP: float = 1_700_000_000.0


def test_battery_voltage_critical() -> None:
    event = check_battery_voltage(21.9, TIMESTAMP)
    assert event is not None
    assert event.severity == Severity.CRITICAL
    assert event.subsystem == "EPS"
    assert event.value == 21.9


def test_battery_voltage_warning() -> None:
    event = check_battery_voltage(23.5, TIMESTAMP)
    assert event is not None
    assert event.severity == Severity.WARNING


def test_battery_voltage_nominal() -> None:
    assert check_battery_voltage(24.0, TIMESTAMP) is None
    assert check_battery_voltage(26.0, TIMESTAMP) is None


def test_wheel_speed_critical() -> None:
    event = check_wheel_speed(4801.0, TIMESTAMP)
    assert event is not None
    assert event.severity == Severity.CRITICAL
    assert event.subsystem == "ADCS"


def test_wheel_speed_warning() -> None:
    event = check_wheel_speed(4600.0, TIMESTAMP)
    assert event is not None
    assert event.severity == Severity.WARNING


def test_wheel_speed_boundary_not_critical() -> None:
    event = check_wheel_speed(4800.0, TIMESTAMP)
    assert event is not None
    assert event.severity == Severity.WARNING


def test_wheel_speed_nominal() -> None:
    assert check_wheel_speed(4500.0, TIMESTAMP) is None
    assert check_wheel_speed(1000.0, TIMESTAMP) is None


def test_bus_temperature_critical() -> None:
    event = check_bus_temperature(43.1, TIMESTAMP)
    assert event is not None
    assert event.severity == Severity.CRITICAL
    assert event.subsystem == "TCS"


def test_bus_temperature_warning() -> None:
    event = check_bus_temperature(41.0, TIMESTAMP)
    assert event is not None
    assert event.severity == Severity.WARNING


def test_bus_temperature_boundary_not_critical() -> None:
    event = check_bus_temperature(43.0, TIMESTAMP)
    assert event is not None
    assert event.severity == Severity.WARNING


def test_bus_temperature_nominal() -> None:
    assert check_bus_temperature(40.0, TIMESTAMP) is None
    assert check_bus_temperature(25.0, TIMESTAMP) is None


def test_evaluate_frame_all_nominal() -> None:
    frame = {
        "timestamp": TIMESTAMP,
        "battery_voltage_v": 25.0,
        "reaction_wheel_speed_rpm": 2000.0,
        "bus_temperature_c": 25.0,
    }
    assert evaluate_frame(frame) == []


def test_evaluate_frame_multiple_breaches() -> None:
    frame = {
        "timestamp": TIMESTAMP,
        "battery_voltage_v": 21.0,
        "reaction_wheel_speed_rpm": 4900.0,
        "bus_temperature_c": 44.0,
    }
    events = evaluate_frame(frame)
    assert len(events) == 3
    assert {event.subsystem for event in events} == {"EPS", "ADCS", "TCS"}
    assert all(event.severity == Severity.CRITICAL for event in events)


def test_evaluate_frame_event_has_required_fields() -> None:
    frame = {
        "timestamp": TIMESTAMP,
        "battery_voltage_v": 20.0,
        "reaction_wheel_speed_rpm": 1000.0,
        "bus_temperature_c": 25.0,
    }
    events = evaluate_frame(frame)
    assert len(events) == 1
    event = events[0]
    assert event.timestamp == TIMESTAMP
    assert event.subsystem == "EPS"
    assert event.severity == Severity.CRITICAL
    assert "Battery voltage" in event.message
    assert event.value == 20.0
    assert "CRITICAL" in event.formatted()
    assert "EPS" in event.formatted()
