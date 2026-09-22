"""LEO CubeSat telemetry simulator streaming JSON frames over WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import asdict, dataclass

import websockets
from websockets.asyncio.server import ServerConnection

HOST: str = "localhost"
PORT: int = 8765
FRAME_INTERVAL_SECONDS: float = 1.0

BATTERY_VOLTAGE_MIN: float = 22.0
BATTERY_VOLTAGE_MAX: float = 28.0
WHEEL_SPEED_MIN_RPM: float = 0.0
WHEEL_SPEED_MAX_RPM: float = 5000.0
BUS_TEMP_MIN_C: float = 15.0
BUS_TEMP_MAX_C: float = 45.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("cubesat_sim")


@dataclass(slots=True)
class TelemetryFrame:
    """Single CCSDS-style telemetry packet payload for the CubeSat bus."""

    timestamp: float
    battery_voltage_v: float
    reaction_wheel_speed_rpm: float
    bus_temperature_c: float

    def to_json(self) -> str:
        return json.dumps(asdict(self))


class TelemetryGenerator:
    """Generates bounded random-walk telemetry values for each parameter."""

    def __init__(self) -> None:
        self._battery_voltage_v: float = random.uniform(
            BATTERY_VOLTAGE_MIN, BATTERY_VOLTAGE_MAX
        )
        self._wheel_speed_rpm: float = random.uniform(
            WHEEL_SPEED_MIN_RPM, WHEEL_SPEED_MAX_RPM
        )
        self._bus_temperature_c: float = random.uniform(
            BUS_TEMP_MIN_C, BUS_TEMP_MAX_C
        )

    @staticmethod
    def _walk(value: float, low: float, high: float, step: float) -> float:
        value += random.uniform(-step, step)
        return max(low, min(high, value))

    def next_frame(self) -> TelemetryFrame:
        self._battery_voltage_v = self._walk(
            self._battery_voltage_v, BATTERY_VOLTAGE_MIN, BATTERY_VOLTAGE_MAX, 0.2
        )
        self._wheel_speed_rpm = self._walk(
            self._wheel_speed_rpm, WHEEL_SPEED_MIN_RPM, WHEEL_SPEED_MAX_RPM, 150.0
        )
        self._bus_temperature_c = self._walk(
            self._bus_temperature_c, BUS_TEMP_MIN_C, BUS_TEMP_MAX_C, 0.5
        )
        return TelemetryFrame(
            timestamp=time.time(),
            battery_voltage_v=round(self._battery_voltage_v, 3),
            reaction_wheel_speed_rpm=round(self._wheel_speed_rpm, 1),
            bus_temperature_c=round(self._bus_temperature_c, 2),
        )


async def stream_telemetry(connection: ServerConnection) -> None:
    """Continuously push telemetry frames to a connected client until it disconnects."""
    client = connection.remote_address
    logger.info("Client connected: %s", client)
    generator = TelemetryGenerator()
    try:
        while True:
            frame = generator.next_frame()
            await connection.send(frame.to_json())
            await asyncio.sleep(FRAME_INTERVAL_SECONDS)
    except websockets.ConnectionClosed:
        logger.info("Client disconnected: %s", client)


async def main() -> None:
    logger.info("Starting CubeSat telemetry simulator on ws://%s:%d", HOST, PORT)
    async with websockets.serve(stream_telemetry, HOST, PORT):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("CubeSat telemetry simulator stopped")
