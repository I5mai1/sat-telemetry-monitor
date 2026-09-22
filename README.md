# LEO CubeSat Telemetry Monitor & OOL Alarm Engine

A local, end-to-end simulation of a small satellite ground segment: a Low Earth
Orbit (LEO) CubeSat telemetry generator, a WebSocket downlink, an Out-of-Limits
(OOL) alarm evaluation engine, and a real-time Streamlit ground control
display. Built as a reference implementation of ECSS/CCSDS-aligned telemetry
monitoring concepts for Power (EPS), Attitude & Orbit Control (AOCS), and
Thermal (TCS) subsystems.

---

## Table of Contents

1. [Project Overview & Architecture](#1-project-overview--architecture)
2. [Subsystem Telemetry Limits & Alarm Rules](#2-subsystem-telemetry-limits--alarm-rules)
3. [Ground Segment & ECSS/CCSDS Standards Context](#3-ground-segment--ecsscccsds-standards-context)
4. [Installation & Local Execution Guide](#4-installation--local-execution-guide)
5. [Repository Directory Structure](#5-repository-directory-structure)

---

## 1. Project Overview & Architecture

This project simulates the telemetry chain of a CubeSat mission's ground
segment end to end, entirely on localhost — no hardware, RF front end, or
external ground station required. It exists to demonstrate how raw spacecraft
telemetry flows from onboard acquisition through downlink, limit checking, and
operator display, using the same conceptual stages a real mission control
system (MCS) would implement.

### Pipeline

```
 ┌──────────────┐     WebSocket      ┌──────────────────┐     evaluate_frame()   ┌──────────────────┐
 │   sim.py     │  ws://localhost   │   alarms.py       │   AlarmEvent list      │    app.py         │
 │  Telemetry   │ ───────8765─────► │   OOL Alarm       │ ─────────────────────► │  Streamlit UI     │
 │  Simulator   │   JSON frames     │   Engine          │                        │  Ground Control    │
 │ (WS Server)  │                   │  (pure functions) │                        │  Display (WS       │
 └──────────────┘                   └──────────────────┘                        │  Client)            │
                                                                                  └──────────────────┘
```

| Stage | Module | Role |
|---|---|---|
| **1. Onboard Acquisition (simulated)** | `sim.py` | Generates bounded random-walk values for battery voltage, reaction wheel speed, and bus temperature; packages them into a timestamped JSON telemetry frame once per second. |
| **2. Downlink** | `sim.py` (`websockets.serve`) | Serves the live telemetry stream over a local WebSocket server at `ws://localhost:8765`, broadcasting one frame per second to any connected client. |
| **3. OOL Alarm Engine** | `alarms.py` | A dependency-free, pure-function module that ingests each telemetry frame and evaluates it against fixed WARNING/CRITICAL Out-of-Limits thresholds per subsystem, producing structured `AlarmEvent` records. |
| **4. Ground Control Display** | `app.py` | A Streamlit dashboard that connects to `sim.py` as a WebSocket client, streams frames through `alarms.py` in real time, and renders KPI cards, live trend charts, and a color-coded alarm matrix. |

### Design principles

- **Decoupled stages.** `sim.py` knows nothing about alarms or UI; `alarms.py`
  knows nothing about WebSockets or Streamlit — it is pure evaluation logic
  over a telemetry dict, independently unit-tested in `test_alarms.py`.
  `app.py` is the only module that wires the two together.
- **Push-based telemetry.** The simulator is the WebSocket *server*; the
  dashboard is a *client*, mirroring how a real spacecraft/ground-station link
  pushes telemetry downstream to mission control rather than the operator
  display polling the spacecraft.
- **Stateless frame evaluation.** Each frame is evaluated independently against
  fixed limits (no smoothing or debounce), matching how a first-stage OOL
  check typically operates before any higher-level alarm-persistence logic is
  applied.

---

## 2. Subsystem Telemetry Limits & Alarm Rules

Each telemetry parameter maps to one spacecraft subsystem and is evaluated
independently, per frame, against two severity thresholds. `CRITICAL` is
always checked before `WARNING` so a single parameter never raises both for
the same frame (the tighter limit takes precedence).

| Subsystem | Parameter | Nominal Range | WARNING | CRITICAL | Direction |
|---|---|---|---|---|---|
| **EPS** (Electrical Power Subsystem) | Battery Voltage | 22 V – 28 V | < 24 V | < 22 V | Low-voltage (undervoltage) |
| **ADCS** (Attitude Determination & Control Subsystem) | Reaction Wheel Speed | 0 – 5000 RPM | > 4500 RPM | > 4800 RPM | High-speed (overspeed / saturation risk) |
| **TCS** (Thermal Control Subsystem) | Bus Temperature | 15 °C – 45 °C | > 40 °C | > 43 °C | High-temperature (overheat) |

### Evaluation logic (`alarms.py`)

- `check_battery_voltage(value, timestamp)` — undervoltage check:
  `value < 22.0` → `CRITICAL`; `elif value < 24.0` → `WARNING`; otherwise `None`.
- `check_wheel_speed(value, timestamp)` — overspeed check:
  `value > 4800.0` → `CRITICAL`; `elif value > 4500.0` → `WARNING`; otherwise `None`.
- `check_bus_temperature(value, timestamp)` — overheat check:
  `value > 43.0` → `CRITICAL`; `elif value > 40.0` → `WARNING`; otherwise `None`.
- `evaluate_frame(frame)` — runs all three checks against one telemetry frame
  and returns a `list[AlarmEvent]` containing only the parameters currently
  out of limits (an empty list means the frame is fully nominal).

Each `AlarmEvent` carries: `timestamp`, `subsystem` (`EPS`/`ADCS`/`TCS`),
`severity` (`WARNING`/`CRITICAL`), a human-readable `message`, and the
breaching `value` — the same fields rendered in the dashboard's Alarm Matrix
and used by `test_alarms.py` to assert correct triggering at every boundary.

---

## 3. Ground Segment & ECSS/CCSDS Standards Context

This project models a simplified **ground segment monitoring function** in the
spirit of the standards a real mission operations team would work against:

- **CCSDS (Consultative Committee for Space Data Systems)** defines the packet
  and space link protocols used to move telemetry from spacecraft to ground
  (e.g. CCSDS 133.0-B, Space Packet Protocol). `sim.py`'s `TelemetryFrame`
  plays the role of a decoded telemetry source packet: a timestamped payload
  of discrete parameter values, analogous to a CCSDS Space Packet's data
  field after unpacking.
- **ECSS-E-ST-70-41 (Telemetry and Telecommand Packet Utilization)** defines
  how individual parameters within that payload are monitored on the ground,
  including the concept this project implements directly: **Out-of-Limits
  (OOL) checking**, where each monitored parameter is compared against
  operator-defined WARNING and CRITICAL limit pairs, and a limit violation
  raises an event/alarm for the operator — exactly the role `alarms.py` and
  the Alarm Matrix in `app.py` fulfill.
- **Subsystem naming** (`EPS`, `ADCS`, `TCS`) follows conventional spacecraft
  subsystem abbreviations used throughout ECSS documentation and mission
  operations, so alarm output reads the way a flight controller's console
  would tag it.

**Scope note:** this is an educational/demonstration implementation. It does
not implement actual CCSDS packet framing (e.g. CCSDS primary/secondary
headers, CRC, Reed–Solomon/convolutional coding) or the full ECSS OOL model
(e.g. persistence/debounce counts, limit sets per operational mode, or
telecommand-side verification) — telemetry is exchanged as plain JSON over a
local WebSocket, and each frame is evaluated statelessly. The architecture and
terminology are aligned with these standards to reflect real ground segment
concepts; use this project as a functional demonstration, not as flight or
ground software.

---

## 4. Installation & Local Execution Guide

### Prerequisites

- **Python 3.11+** (see `CLAUDE.md` for the project's language/style standards)
- `pip`

### 4.1 Create and activate a virtual environment

```powershell
# From the project root
python -m venv venv

# Activate (Windows PowerShell / cmd)
venv\Scripts\activate

# Activate (Git Bash / WSL / macOS / Linux)
source venv/Scripts/activate   # Git Bash on Windows
source venv/bin/activate       # macOS/Linux
```

### 4.2 Install dependencies

```bash
pip install -r requirements.txt
```

This installs `streamlit`, `websockets`, `pandas`, and `pytest` (plus
transitive dependencies such as `altair`, which Streamlit uses for charting).

### 4.3 Run the unit tests

```bash
python -m pytest test_alarms.py -v
```

Expected result: all OOL threshold tests pass (CRITICAL, WARNING, nominal, and
boundary cases for all three subsystems, plus multi-breach frame evaluation).

### 4.4 Run the telemetry simulator

In **Terminal 1**, from the project root with the venv activated:

```bash
python sim.py
```

This starts the WebSocket telemetry server at `ws://localhost:8765` and logs
start/stop and client connect/disconnect events to the console. Leave this
running.

### 4.5 Run the ground control dashboard

In **Terminal 2**, from the project root with the venv activated:

```bash
streamlit run app.py
```

Streamlit will print a local URL (typically `http://localhost:8501`) — open it
in a browser. The dashboard connects to `sim.py` automatically, evaluates
every incoming frame through `alarms.py`, and displays:

1. **Top KPI Summary Cards** — latest Battery Voltage, Reaction Wheel Speed,
   Bus Temperature, and current active alarm counts, each color-coded by
   severity.
2. **Real-Time Telemetry Charts** — live-updating line charts for Power
   (EPS), AOCS reaction wheel speed, and thermal trends.
3. **Active OOL Alarm Matrix** — a color-coded table (yellow = WARNING,
   red = CRITICAL) of alarm events with timestamps, subsystem tags, and
   breaching values.

> **Startup order:** start `sim.py` before `app.py` so the WebSocket server is
> listening when the dashboard connects. If started out of order, `app.py`
> will show a "Connecting…" / "Disconnected — retrying…" status and recover
> automatically once `sim.py` is up.

### 4.6 Stopping

Press `Ctrl+C` in each terminal. `sim.py` logs a shutdown message; Streamlit
shuts down its local server on interrupt.

---

## 5. Repository Directory Structure

```
Project/
├── CLAUDE.md            # Project coding standards (Python 3.11+, modular
│                         # structure, type hinting, ECSS/CCSDS conventions)
├── README.md             # This file
├── requirements.txt      # Runtime dependencies: streamlit, websockets,
│                         # pandas, pytest
│
├── sim.py                # Stage 1–2: CubeSat telemetry simulator +
│                         # WebSocket server (ws://localhost:8765)
├── alarms.py              # Stage 3: OOL alarm evaluation engine
│                         # (pure functions, AlarmEvent, Severity enum)
├── test_alarms.py         # Pytest unit tests for alarms.py threshold logic
├── app.py                 # Stage 4: Streamlit ground control dashboard
│                         # (WebSocket client + alarms.py consumer)
│
└── venv/                  # Local Python 3.11 virtual environment
                          # (not version-controlled)
```

### Module responsibilities at a glance

| File | Type hints | External deps | Responsibility |
|---|---|---|---|
| `sim.py` | Yes | `websockets` | Telemetry generation + WebSocket server |
| `alarms.py` | Yes | `websockets` (client helper only) | OOL threshold evaluation, `AlarmEvent`/`Severity` model |
| `test_alarms.py` | Yes | `pytest` | Unit tests for every alarm threshold and boundary |
| `app.py` | Yes | `streamlit`, `pandas`, `altair`, `websockets` | WebSocket client, background telemetry buffer, dashboard UI |
