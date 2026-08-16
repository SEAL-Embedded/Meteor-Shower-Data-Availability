"""Great-circle distance.

Used to decide which reported events are near enough to our instruments to be worth keeping. The
spherical approximation is good to about 0.5% -- some tens of kilometres at the ranges we filter on,
which is far below the uncertainty in an eyewitness-derived fireball position.
"""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0088


def haversine_km(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    """Distance along the Earth's surface between two points, in kilometres."""
    lat_a, lon_a, lat_b, lon_b = map(
        radians, (latitude_a, longitude_a, latitude_b, longitude_b)
    )
    d_lat = lat_b - lat_a
    d_lon = lon_b - lon_a
    h = sin(d_lat / 2) ** 2 + cos(lat_a) * cos(lat_b) * sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(min(1.0, h)))
