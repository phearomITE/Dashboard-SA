"""Offline WGS84 point-in-polygon lookup against the bundled Cambodia COD-AB."""
import gzip
import json
from collections import defaultdict
from functools import lru_cache
from math import asin, cos, floor, radians, sin, sqrt
from pathlib import Path

BOUNDARIES = Path(__file__).resolve().parents[1] / "data" / "kh_boundaries.json.gz"


@lru_cache(maxsize=1)
def boundary_index():
    with gzip.open(BOUNDARIES, "rt", encoding="utf-8") as stream:
        data = json.load(stream)
    cells = defaultdict(list)
    for row in data["communes"]:
        for polygon in row["polygons"]:
            xs, ys = zip(*polygon[0])
            bounds = min(xs), min(ys), max(xs), max(ys)
            for x in range(floor(bounds[0]*10), floor(bounds[2]*10)+1):
                for y in range(floor(bounds[1]*10), floor(bounds[3]*10)+1):
                    cells[x, y].append((bounds, polygon, row))
    return cells, data["provinces"]


def in_ring(x, y, ring):
    inside = False
    ax, ay = ring[-1]
    for bx, by in ring:
        if (ay > y) != (by > y) and x < (bx-ax)*(y-ay)/(by-ay) + ax:
            inside = not inside
        ax, ay = bx, by
    return inside


@lru_cache(maxsize=8192)
def administrative_area(lat, lon):
    cells, _ = boundary_index()
    for (west, south, east, north), rings, row in cells.get((floor(lon*10), floor(lat*10)), ()):
        if west <= lon <= east and south <= lat <= north and in_ring(lon, lat, rings[0]):
            if not any(in_ring(lon, lat, hole) for hole in rings[1:]):
                return tuple(row["names"])
    return None, None, None


def distance_km(start, end):
    lat1, lon1 = map(radians, start)
    lat2, lon2 = map(radians, end)
    a = sin((lat2-lat1)/2)**2 + cos(lat1)*cos(lat2)*sin((lon2-lon1)/2)**2
    return 12742.0176 * asin(sqrt(min(1, a)))


def boundary_map(west, south, east, north, detail):
    cells, provinces = boundary_index()
    if detail:
        rows = {}
        # Clip to the country before iterating, even for worldwide viewports.
        for x in range(max(1020, floor(west*10)), min(1081, floor(east*10)+1)):
            for y in range(max(90, floor(south*10)), min(150, floor(north*10)+1)):
                for bounds, _, row in cells.get((x, y), ()):
                    if bounds[2]>=west and bounds[0]<=east and bounds[3]>=south and bounds[1]<=north:
                        rows[row["code"]] = row
        features = rows.values()
    else:
        features = provinces
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": row["names"][-1], "code": row["code"], "center": row["center"]},
         "geometry": {"type": "MultiPolygon", "coordinates": row["polygons"]}} for row in features]}
