"""Streamlit ground control dashboard for CubeSat telemetry.

Connects to the sim.py WebSocket feed, evaluates each frame against the OOL
limits in alarms.py, and renders KPI cards, live trend charts, and a
color-coded alarm matrix. Run alongside sim.py:

    python sim.py
    streamlit run app.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import deque
from datetime import datetime, timezone

import altair as alt
import pandas as pd
import streamlit as st
import websockets

from alarms import AlarmEvent, Severity, evaluate_frame

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("cubesat_dashboard")

WS_URI: str = "ws://localhost:8765"
RECONNECT_DELAY_SECONDS: float = 2.0
MAX_FRAME_HISTORY: int = 180
MAX_ALARM_HISTORY: int = 200
REFRESH_INTERVAL: str = "1s"

# Colors below come from the project's validated status/categorical palette.
# Categorical slots 1/2/3 (front-loaded, CVD-safe as a set) — one per chart.
COLOR_BATTERY: str = "#2a78d6"  # slot 1 — blue
COLOR_TEMP: str = "#eb6834"  # slot 2 — orange
COLOR_WHEEL: str = "#1baf7a"  # slot 3 — aqua

# Status palette (fixed, never reused for series identity).
STATUS_GOOD: str = "#0ca30c"
STATUS_WARNING: str = "#fab219"
STATUS_CRITICAL: str = "#d03b3b"


class TelemetryStore:
    """Thread-safe rolling buffer shared between the WS client thread and the UI."""

    def __init__(self, max_frames: int = MAX_FRAME_HISTORY, max_alarms: int = MAX_ALARM_HISTORY) -> None:
        self._lock = threading.Lock()
        self._frames: deque = deque(maxlen=max_frames)
        self._alarms: deque = deque(maxlen=max_alarms)
        self.status: str = "CONNECTING"

    def add_frame(self, frame: dict) -> None:
        with self._lock:
            self._frames.append(frame)

    def add_alarms(self, events: list) -> None:
        if not events:
            return
        with self._lock:
            self._alarms.extend(events)

    def set_status(self, status: str) -> None:
        with self._lock:
            self.status = status

    def snapshot(self) -> tuple:
        with self._lock:
            return list(self._frames), list(self._alarms), self.status


async def _stream_loop(store: TelemetryStore, uri: str) -> None:
    while True:
        try:
            store.set_status("CONNECTING")
            async with websockets.connect(uri) as connection:
                store.set_status("CONNECTED")
                logger.info("Dashboard connected to telemetry stream at %s", uri)
                async for raw_frame in connection:
                    frame = json.loads(raw_frame)
                    store.add_frame(frame)
                    store.add_alarms(evaluate_frame(frame))
        except (OSError, websockets.WebSocketException) as exc:
            store.set_status("DISCONNECTED")
            logger.warning(
                "Telemetry stream unavailable (%s); retrying in %.0fs", exc, RECONNECT_DELAY_SECONDS
            )
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)


def _start_background_client(store: TelemetryStore, uri: str) -> None:
    def _runner() -> None:
        asyncio.run(_stream_loop(store, uri))

    thread = threading.Thread(target=_runner, name="telemetry-ws-client", daemon=True)
    thread.start()
    logger.info("Background telemetry client thread started")


@st.cache_resource
def get_store() -> TelemetryStore:
    store = TelemetryStore()
    _start_background_client(store, WS_URI)
    return store


def _inject_css() -> None:
    st.markdown(
        """
        <style>
        .kpi-card {
            background: #fcfcfb;
            border: 1px solid rgba(11,11,11,0.10);
            border-left: 4px solid #0ca30c;
            border-radius: 8px;
            padding: 0.9rem 1.1rem;
            margin-bottom: 0.5rem;
        }
        .kpi-label {
            font-size: 0.8rem;
            color: #898781;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        .kpi-value {
            font-size: 1.7rem;
            font-weight: 600;
            color: #0b0b0b;
            margin: 0.15rem 0;
        }
        .kpi-status {
            font-size: 0.8rem;
            font-weight: 600;
            letter-spacing: 0.03em;
        }
        @media (prefers-color-scheme: dark) {
            .kpi-card { background: #1a1a19; border-color: rgba(255,255,255,0.10); }
            .kpi-label { color: #898781; }
            .kpi-value { color: #ffffff; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _severity_color(severity) -> str:
    if severity == Severity.CRITICAL:
        return STATUS_CRITICAL
    if severity == Severity.WARNING:
        return STATUS_WARNING
    return STATUS_GOOD


def _severity_label(severity) -> str:
    return severity.value if severity is not None else "NOMINAL"


def _kpi_card_html(label: str, value: str, severity) -> str:
    color = _severity_color(severity)
    status_label = _severity_label(severity)
    return (
        f'<div class="kpi-card" style="border-left-color: {color};">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}</div>'
        f'<div class="kpi-status" style="color: {color};">&#9679; {status_label}</div>'
        f"</div>"
    )


def _build_dataframe(frames: list) -> pd.DataFrame:
    df = pd.DataFrame(frames)
    df["time"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    return df


def _line_chart(df: pd.DataFrame, y_field: str, y_title: str, color: str) -> alt.Chart:
    return (
        alt.Chart(df)
        .mark_line(color=color, strokeWidth=2, point=alt.OverlayMarkDef(color=color, size=25))
        .encode(
            x=alt.X("time:T", title=None),
            y=alt.Y(f"{y_field}:Q", title=y_title, scale=alt.Scale(zero=False)),
            tooltip=[
                alt.Tooltip("time:T", title="Time", format="%H:%M:%S"),
                alt.Tooltip(f"{y_field}:Q", title=y_title, format=".2f"),
            ],
        )
        .properties(height=220)
        .interactive()
    )


def _alarm_dataframe(events: list) -> pd.DataFrame:
    rows = [
        {
            "Time (UTC)": datetime.fromtimestamp(event.timestamp, tz=timezone.utc).strftime("%H:%M:%S"),
            "Subsystem": event.subsystem,
            "Severity": event.severity.value,
            "Message": event.message,
            "Value": event.value,
        }
        for event in reversed(events)
    ]
    return pd.DataFrame(rows)


def _style_alarm_row(row: pd.Series) -> list:
    if row["Severity"] == Severity.CRITICAL.value:
        background = "rgba(208, 59, 59, 0.28)"
    elif row["Severity"] == Severity.WARNING.value:
        background = "rgba(250, 178, 25, 0.28)"
    else:
        background = "transparent"
    return [f"background-color: {background}"] * len(row)


@st.fragment(run_every=REFRESH_INTERVAL)
def render_dashboard() -> None:
    store = get_store()
    frames, alarm_history, status = store.snapshot()

    status_captions = {
        "CONNECTED": "🟢 Connected to telemetry stream",
        "CONNECTING": "🟡 Connecting to telemetry stream…",
        "DISCONNECTED": "🔴 Disconnected — retrying…",
    }
    st.caption(f"{status_captions.get(status, status)} · `{WS_URI}`")

    if not frames:
        st.info("Waiting for telemetry frames from sim.py — start it with `python sim.py`.")
        return

    latest = frames[-1]
    current_alarms = evaluate_frame(latest)
    severity_by_subsystem = {event.subsystem: event.severity for event in current_alarms}
    n_critical = sum(1 for event in current_alarms if event.severity == Severity.CRITICAL)
    n_warning = sum(1 for event in current_alarms if event.severity == Severity.WARNING)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(
            _kpi_card_html(
                "Battery Voltage",
                f"{latest['battery_voltage_v']:.2f} V",
                severity_by_subsystem.get("EPS"),
            ),
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            _kpi_card_html(
                "Reaction Wheel Speed",
                f"{latest['reaction_wheel_speed_rpm']:.0f} RPM",
                severity_by_subsystem.get("ADCS"),
            ),
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown(
            _kpi_card_html(
                "Bus Temperature",
                f"{latest['bus_temperature_c']:.2f} °C",
                severity_by_subsystem.get("TCS"),
            ),
            unsafe_allow_html=True,
        )
    with col4:
        if n_critical:
            alarm_severity = Severity.CRITICAL
        elif n_warning:
            alarm_severity = Severity.WARNING
        else:
            alarm_severity = None
        st.markdown(
            _kpi_card_html("Active Alarms", f"{n_critical} CRIT / {n_warning} WARN", alarm_severity),
            unsafe_allow_html=True,
        )

    st.divider()

    df = _build_dataframe(frames)
    st.subheader("Real-Time Telemetry")
    chart_col1, chart_col2, chart_col3 = st.columns(3)
    with chart_col1:
        st.markdown("**Power — Battery Voltage (V)**")
        st.altair_chart(
            _line_chart(df, "battery_voltage_v", "Voltage (V)", COLOR_BATTERY),
            use_container_width=True,
        )
    with chart_col2:
        st.markdown("**AOCS — Reaction Wheel Speed (RPM)**")
        st.altair_chart(
            _line_chart(df, "reaction_wheel_speed_rpm", "Speed (RPM)", COLOR_WHEEL),
            use_container_width=True,
        )
    with chart_col3:
        st.markdown("**Thermal — Bus Temperature (°C)**")
        st.altair_chart(
            _line_chart(df, "bus_temperature_c", "Temp (°C)", COLOR_TEMP),
            use_container_width=True,
        )

    st.divider()
    st.subheader("Active Out-of-Limits Alarm Matrix")
    if not alarm_history:
        st.success("No OOL events recorded this session — all systems nominal.")
    else:
        alarm_df = _alarm_dataframe(alarm_history)
        st.dataframe(
            alarm_df.style.apply(_style_alarm_row, axis=1),
            use_container_width=True,
            hide_index=True,
        )


def main() -> None:
    st.set_page_config(page_title="CubeSat Ground Control", page_icon="🛰️", layout="wide")
    st.title("🛰️ CubeSat Ground Control")
    _inject_css()
    render_dashboard()


if __name__ == "__main__":
    main()
