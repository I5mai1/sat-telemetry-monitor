"""SGP4 orbit propagation and ground-track geometry shared by sim.py and app.py."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sgp4.api import Satrec, jday

logger = logging.getLogger("cubesat_orbit")

EARTH_RADIUS_EQUATORIAL_KM: float = 6378.137
EARTH_FLATTENING: float = 1.0 / 298.257223563
EARTH_ECCENTRICITY_SQUARED: float = EARTH_FLATTENING * (2.0 - EARTH_FLATTENING)

# ISS (ZARYA) reference TLE — a realistic LEO orbit (51.6-deg inclination,
# ~420 km altitude) used to drive this simulation. SGP4 accuracy degrades the
# further "now" is from the TLE epoch (epoch 2026-09-22 here); refresh from
# celestrak.org (e.g. https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE)
# periodically to keep real-world tracking accuracy.
TLE_LINE1: str = "1 25544U 98067A   26265.14225571  .00007091  00000+0  13570-3 0  9993"
TLE_LINE2: str = "2 25544  51.6312 180.2431 0004771 167.1129 192.9982 15.49222361586784"

GROUND_TRACK_WINDOW_MINUTES: int = 45
GROUND_TRACK_STEP_SECONDS: int = 30


@dataclass(slots=True)
class OrbitState:
    """Geodetic position and inertial speed of the satellite at one instant."""

    latitude_deg: float
    longitude_deg: float
    altitude_km: float
    speed_km_s: float


def _gmst_radians(jd_ut1: float) -> float:
    """Greenwich Mean Sidereal Time (IAU 1982 model), in radians."""
    t = (jd_ut1 - 2451545.0) / 36525.0
    theta_deg = (
        280.46061837
        + 360.98564736629 * (jd_ut1 - 2451545.0)
        + 0.000387933 * t * t
        - (t**3) / 38710000.0
    ) % 360.0
    return math.radians(theta_deg)


def _teme_to_ecef(position_teme: tuple, gmst: float) -> tuple:
    """Rotate a TEME position vector into Earth-fixed (ECEF) coordinates."""
    x, y, z = position_teme
    cos_g, sin_g = math.cos(gmst), math.sin(gmst)
    return x * cos_g + y * sin_g, -x * sin_g + y * cos_g, z


def _ecef_to_geodetic(x: float, y: float, z: float) -> tuple:
    """WGS84 ECEF (km) -> geodetic latitude/longitude (deg) and altitude (km)."""
    longitude = math.atan2(y, x)
    p = math.hypot(x, y)
    latitude = math.atan2(z, p * (1.0 - EARTH_ECCENTRICITY_SQUARED))
    altitude = 0.0
    for _ in range(5):
        sin_lat = math.sin(latitude)
        n = EARTH_RADIUS_EQUATORIAL_KM / math.sqrt(1.0 - EARTH_ECCENTRICITY_SQUARED * sin_lat * sin_lat)
        altitude = p / math.cos(latitude) - n
        latitude = math.atan2(z, p * (1.0 - EARTH_ECCENTRICITY_SQUARED * n / (n + altitude)))
    return math.degrees(latitude), math.degrees(longitude), altitude


class OrbitPropagator:
    """SGP4-based propagator for a fixed reference TLE."""

    def __init__(self, tle_line1: str = TLE_LINE1, tle_line2: str = TLE_LINE2) -> None:
        self._satellite = Satrec.twoline2rv(tle_line1, tle_line2)

    def state_at(self, moment: datetime):
        jd, fr = jday(
            moment.year,
            moment.month,
            moment.day,
            moment.hour,
            moment.minute,
            moment.second + moment.microsecond / 1_000_000,
        )
        error_code, position_teme, velocity_teme = self._satellite.sgp4(jd, fr)
        if error_code != 0:
            logger.warning("SGP4 propagation error (code %d) at %s", error_code, moment.isoformat())
            return None

        gmst = _gmst_radians(jd + fr)
        x, y, z = _teme_to_ecef(position_teme, gmst)
        latitude_deg, longitude_deg, altitude_km = _ecef_to_geodetic(x, y, z)
        speed_km_s = math.sqrt(sum(component * component for component in velocity_teme))
        return OrbitState(latitude_deg, longitude_deg, altitude_km, speed_km_s)

    def current_state(self):
        return self.state_at(datetime.now(timezone.utc))

    def ground_track(
        self,
        center: datetime | None = None,
        window_minutes: int = GROUND_TRACK_WINDOW_MINUTES,
        step_seconds: int = GROUND_TRACK_STEP_SECONDS,
    ) -> list:
        """Sample orbit states from `window_minutes` in the past to `window_minutes` ahead.

        Returns a list of (offset_seconds, OrbitState) pairs, offset_seconds
        negative for past samples, zero for "now", positive for future samples.
        """
        center = center or datetime.now(timezone.utc)
        half_span = window_minutes * 60
        samples = []
        for offset in range(-half_span, half_span + 1, step_seconds):
            state = self.state_at(center + timedelta(seconds=offset))
            if state is not None:
                samples.append((offset, state))
        return samples


def destination_point(lat_deg: float, lon_deg: float, bearing_deg: float, angular_distance_rad: float) -> tuple:
    """Great-circle destination point given a start, bearing, and angular distance."""
    lat1, lon1, bearing = math.radians(lat_deg), math.radians(lon_deg), math.radians(bearing_deg)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular_distance_rad)
        + math.cos(lat1) * math.sin(angular_distance_rad) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular_distance_rad) * math.cos(lat1),
        math.cos(angular_distance_rad) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), (math.degrees(lon2) + 540.0) % 360.0 - 180.0


def footprint_polygon(
    ground_lat_deg: float,
    ground_lon_deg: float,
    satellite_altitude_km: float,
    min_elevation_deg: float = 10.0,
    num_points: int = 72,
) -> list:
    """Ground-station visibility circle for a given minimum elevation angle.

    Returns a closed ring of [lon, lat] points suitable for a pydeck PolygonLayer.
    """
    elevation = math.radians(min_elevation_deg)
    earth_over_orbit = EARTH_RADIUS_EQUATORIAL_KM / (EARTH_RADIUS_EQUATORIAL_KM + satellite_altitude_km)
    nadir_angle = math.asin(earth_over_orbit * math.cos(elevation))
    central_angle = (math.pi / 2.0) - elevation - nadir_angle

    ring = []
    for i in range(num_points + 1):
        bearing = 360.0 * i / num_points
        lat, lon = destination_point(ground_lat_deg, ground_lon_deg, bearing, central_angle)
        ring.append([lon, lat])
    return ring
