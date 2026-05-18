from __future__ import annotations

import math


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def estimate_walking_minutes(
    lat: float | None,
    lon: float | None,
    target_lat: float,
    target_lon: float,
    walking_speed_kmh: float = 4.8,
    route_factor: float = 1.25,
) -> float | None:
    """Estimate walking time from straight-line distance.

    This does not replace a routing API. It intentionally uses a conservative
    route factor because Bristol walking routes can be hilly and indirect.
    """
    if lat is None or lon is None:
        return None
    straight_km = haversine_km(lat, lon, target_lat, target_lon)
    route_km = straight_km * route_factor
    return round((route_km / walking_speed_kmh) * 60, 1)
