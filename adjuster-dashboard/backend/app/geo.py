"""
Geo proximity. Phase 1 uses haversine in Python — fine for ~40 shops.
Phase 2 swaps this for Azure AI Search geo.distance / PostGIS ST_DWithin
behind the same function signature.
"""
from math import radians, sin, cos, asin, sqrt


def haversine_km(lat1, lng1, lat2, lng2) -> float:
    """Great-circle distance between two points in kilometers."""
    lat1, lng1, lat2, lng2 = map(radians, (lat1, lng1, lat2, lng2))
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))
