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

from orbit import OrbitPropagator, OrbitState

HOST: str = "localhost"
PORT: int = 8765
FRAME_INTERVAL_SECONDS: float = 1.0

BATTERY_VOLTAGE_MIN: float = 22.0
BATTERY_VOLTAGE_MAX: float = 28.0
WHEEL_SPEED_MIN_RPM: float = 0.0
WHEEL_SPEED_MAX_RPM: float = 5000.0
BUS_TEMP_MIN_C: float = 15.0
BUS_TEMP_MAX_C: float = 45.0

# Telecommand effects
AOCS_RESET_RPM: float = 1000.0
SAFE_MODE_VOLTAGE_BOOST_V: float = 3.0
SAFE_MODE_WALK_BIAS: float = 0.15
BUS_TEMP_NOMINAL_MIN_C: float = 15.0
BUS_TEMP_NOMINAL_MAX_C: float = 35.0
HEATER_CORRECTION_FACTOR: float = 0.5

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
    latitude_deg: float
    longitude_deg: float
    altitude_km: float
    orbital_speed_km_s: float

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
        self._orbit = OrbitPropagator()
        initial_orbit = self._orbit.current_state()
        if initial_orbit is None:
            raise RuntimeError("SGP4 propagator failed to compute an initial orbit state")
        self._last_orbit: OrbitState = initial_orbit
        self._safe_mode: bool = False

    @staticmethod
    def _walk(value: float, low: float, high: float, step: float, bias: float = 0.0) -> float:
        value += random.uniform(-step, step) + bias
        return max(low, min(high, value))

    def reset_aocs(self) -> None:
        """RESET_AOCS: zero out momentum build-up by returning the wheel to its nominal speed."""
        self._wheel_speed_rpm = AOCS_RESET_RPM

    def enter_safe_mode(self) -> None:
        """SET_SAFE_MODE: shed payload load — boost voltage now and bias future recharge."""
        self._safe_mode = True
        self._battery_voltage_v = min(
            BATTERY_VOLTAGE_MAX, self._battery_voltage_v + SAFE_MODE_VOLTAGE_BOOST_V
        )

    def toggle_heater(self) -> float:
        """TOGGLE_HEATER: nudge bus temperature halfway back toward the nominal band."""
        target = (BUS_TEMP_NOMINAL_MIN_C + BUS_TEMP_NOMINAL_MAX_C) / 2.0
        delta = (target - self._bus_temperature_c) * HEATER_CORRECTION_FACTOR
        self._bus_temperature_c = max(
            BUS_TEMP_MIN_C, min(BUS_TEMP_MAX_C, self._bus_temperature_c + delta)
        )
        return delta

    def next_frame(self) -> TelemetryFrame:
        battery_bias = SAFE_MODE_WALK_BIAS if self._safe_mode else 0.0
        self._battery_voltage_v = self._walk(
            self._battery_voltage_v, BATTERY_VOLTAGE_MIN, BATTERY_VOLTAGE_MAX, 0.2, bias=battery_bias
        )
        self._wheel_speed_rpm = self._walk(
            self._wheel_speed_rpm, WHEEL_SPEED_MIN_RPM, WHEEL_SPEED_MAX_RPM, 150.0
        )
        self._bus_temperature_c = self._walk(
            self._bus_temperature_c, BUS_TEMP_MIN_C, BUS_TEMP_MAX_C, 0.5
        )

        orbit = self._orbit.current_state()
        if orbit is not None:
            self._last_orbit = orbit
        else:
            orbit = self._last_orbit

        return TelemetryFrame(
            timestamp=time.time(),
            battery_voltage_v=round(self._battery_voltage_v, 3),
            reaction_wheel_speed_rpm=round(self._wheel_speed_rpm, 1),
            bus_temperature_c=round(self._bus_temperature_c, 2),
            latitude_deg=round(orbit.latitude_deg, 5),
            longitude_deg=round(orbit.longitude_deg, 5),
            altitude_km=round(orbit.altitude_km, 3),
            orbital_speed_km_s=round(orbit.speed_km_s, 4),
        )


def _cmd_reset_aocs(generator: TelemetryGenerator) -> str:
    generator.reset_aocs()
    return f"Reaction wheel speed reset to {AOCS_RESET_RPM:.0f} RPM; momentum build-up cleared"


def _cmd_set_safe_mode(generator: TelemetryGenerator) -> str:
    generator.enter_safe_mode()
    return "Payload power shed; battery voltage recharging above 25V"


def _cmd_toggle_heater(generator: TelemetryGenerator) -> str:
    delta = generator.toggle_heater()
    direction = "warming" if delta > 0 else "cooling"
    return f"Thermal control {direction} bus toward nominal range ({BUS_TEMP_NOMINAL_MIN_C:.0f}-{BUS_TEMP_NOMINAL_MAX_C:.0f}C)"


COMMAND_HANDLERS = {
    "RESET_AOCS": _cmd_reset_aocs,
    "SET_SAFE_MODE": _cmd_set_safe_mode,
    "TOGGLE_HEATER": _cmd_toggle_heater,
}


async def _handle_command(connection: ServerConnection, generator: TelemetryGenerator, raw_message: str) -> None:
    received_at = time.time()
    try:
        payload = json.loads(raw_message)
        command = str(payload["command"]).upper()
    except (json.JSONDecodeError, KeyError, TypeError):
        logger.warning("Received malformed telecommand: %r", raw_message)
        await connection.send(json.dumps({
            "type": "TC_ACK",
            "command": None,
            "status": "REJECTED",
            "detail": "Malformed command payload",
            "timestamp": received_at,
        }))
        return

    handler = COMMAND_HANDLERS.get(command)
    if handler is None:
        logger.warning("Received unknown telecommand: %s", command)
        await connection.send(json.dumps({
            "type": "TC_ACK",
            "command": command,
            "status": "REJECTED",
            "detail": f"Unknown command '{command}'",
            "timestamp": received_at,
        }))
        return

    detail = handler(generator)
    logger.info("Telecommand received and executed: %s -> %s", command, detail)
    await connection.send(json.dumps({
        "type": "TC_ACK",
        "command": command,
        "status": "EXECUTED",
        "detail": detail,
        "timestamp": received_at,
    }))


async def _send_telemetry(connection: ServerConnection, generator: TelemetryGenerator) -> None:
    while True:
        frame = generator.next_frame()
        await connection.send(frame.to_json())
        await asyncio.sleep(FRAME_INTERVAL_SECONDS)


async def _receive_commands(connection: ServerConnection, generator: TelemetryGenerator) -> None:
    async for raw_message in connection:
        await _handle_command(connection, generator, raw_message)


async def stream_telemetry(connection: ServerConnection) -> None:
    """Stream telemetry to a client while concurrently accepting its telecommands."""
    client = connection.remote_address
    logger.info("Client connected: %s", client)
    generator = TelemetryGenerator()

    send_task = asyncio.create_task(_send_telemetry(connection, generator))
    receive_task = asyncio.create_task(_receive_commands(connection, generator))
    try:
        done, pending = await asyncio.wait(
            {send_task, receive_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, websockets.ConnectionClosed):
                raise exc
    finally:
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
