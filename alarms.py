"""Out-of-Limits (OOL) evaluation for CubeSat telemetry frames from sim.py."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping

import websockets

logger = logging.getLogger("cubesat_alarms")

SUBSYSTEM_EPS: str = "EPS"
SUBSYSTEM_ADCS: str = "ADCS"
SUBSYSTEM_TCS: str = "TCS"

BATTERY_VOLTAGE_WARNING_V: float = 24.0
BATTERY_VOLTAGE_CRITICAL_V: float = 22.0
WHEEL_SPEED_WARNING_RPM: float = 4500.0
WHEEL_SPEED_CRITICAL_RPM: float = 4800.0
BUS_TEMP_WARNING_C: float = 40.0
BUS_TEMP_CRITICAL_C: float = 43.0


class Severity(str, Enum):
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(slots=True)
class AlarmEvent:
    """A single OOL event raised against one telemetry parameter."""

    timestamp: float
    subsystem: str
    severity: Severity
    message: str
    value: float

    def formatted(self) -> str:
        iso_time = datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat()
        return (
            f"{iso_time} | {self.severity.value:<8} | {self.subsystem:<4} | "
            f"{self.message} | value={self.value}"
        )


def _check_low(
    value: float,
    timestamp: float,
    subsystem: str,
    parameter: str,
    unit: str,
    warning_threshold: float,
    critical_threshold: float,
) -> AlarmEvent | None:
    if value < critical_threshold:
        return AlarmEvent(
            timestamp=timestamp,
            subsystem=subsystem,
            severity=Severity.CRITICAL,
            message=f"{parameter} {value}{unit} below critical threshold ({critical_threshold}{unit})",
            value=value,
        )
    if value < warning_threshold:
        return AlarmEvent(
            timestamp=timestamp,
            subsystem=subsystem,
            severity=Severity.WARNING,
            message=f"{parameter} {value}{unit} below warning threshold ({warning_threshold}{unit})",
            value=value,
        )
    return None


def _check_high(
    value: float,
    timestamp: float,
    subsystem: str,
    parameter: str,
    unit: str,
    warning_threshold: float,
    critical_threshold: float,
) -> AlarmEvent | None:
    if value > critical_threshold:
        return AlarmEvent(
            timestamp=timestamp,
            subsystem=subsystem,
            severity=Severity.CRITICAL,
            message=f"{parameter} {value}{unit} above critical threshold ({critical_threshold}{unit})",
            value=value,
        )
    if value > warning_threshold:
        return AlarmEvent(
            timestamp=timestamp,
            subsystem=subsystem,
            severity=Severity.WARNING,
            message=f"{parameter} {value}{unit} above warning threshold ({warning_threshold}{unit})",
            value=value,
        )
    return None


def check_battery_voltage(value: float, timestamp: float) -> AlarmEvent | None:
    return _check_low(
        value,
        timestamp,
        SUBSYSTEM_EPS,
        "Battery voltage",
        "V",
        BATTERY_VOLTAGE_WARNING_V,
        BATTERY_VOLTAGE_CRITICAL_V,
    )


def check_wheel_speed(value: float, timestamp: float) -> AlarmEvent | None:
    return _check_high(
        value,
        timestamp,
        SUBSYSTEM_ADCS,
        "Reaction wheel speed",
        "RPM",
        WHEEL_SPEED_WARNING_RPM,
        WHEEL_SPEED_CRITICAL_RPM,
    )


def check_bus_temperature(value: float, timestamp: float) -> AlarmEvent | None:
    return _check_high(
        value,
        timestamp,
        SUBSYSTEM_TCS,
        "Bus temperature",
        "C",
        BUS_TEMP_WARNING_C,
        BUS_TEMP_CRITICAL_C,
    )


def evaluate_frame(frame: Mapping[str, float]) -> list[AlarmEvent]:
    """Evaluate a telemetry frame (as produced by sim.py) against all OOL limits."""
    timestamp = float(frame["timestamp"])
    checks = (
        check_battery_voltage(float(frame["battery_voltage_v"]), timestamp),
        check_wheel_speed(float(frame["reaction_wheel_speed_rpm"]), timestamp),
        check_bus_temperature(float(frame["bus_temperature_c"]), timestamp),
    )
    return [event for event in checks if event is not None]


async def monitor(uri: str = "ws://localhost:8765") -> None:
    """Connect to sim.py's WebSocket feed and log OOL events as frames arrive."""
    logger.info("Connecting to telemetry stream at %s", uri)
    async with websockets.connect(uri) as connection:
        logger.info("Connected, monitoring for OOL events")
        try:
            async for raw_frame in connection:
                frame = json.loads(raw_frame)
                for event in evaluate_frame(frame):
                    log_fn = logger.critical if event.severity == Severity.CRITICAL else logger.warning
                    log_fn(event.formatted())
        except websockets.ConnectionClosed:
            logger.info("Telemetry stream closed")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    try:
        asyncio.run(monitor())
    except KeyboardInterrupt:
        logger.info("Alarm monitor stopped")
