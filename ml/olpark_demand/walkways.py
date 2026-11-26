"""Build the walkway graph of Olympic Park that the walking simulation runs on.

The dashboard's first network (``src/data/network.json``) holds three chosen routes. This graph
holds every walkway OpenStreetMap has for the park, so that a crowd can be sent along whichever
way is open and each walkway shows its own load.

    curl -s -H "User-Agent: osm-walk-extract/1.0" --data-urlencode "data@geo/osm/overpass-walkways.txt" \\
        https://overpass-api.de/api/interpreter -o geo/osm/osm-walkways.json          # at the repository root
    python -m olpark_demand.walkways                                                  # in ml/

What is measured and what is assumed:

- Geometry and connections come from OpenStreetMap (© OpenStreetMap contributors, ODbL 1.0).
- A walkway that runs along one of the first network's segments takes that segment's width,
  which was read off aerial imagery (docs/methods/network.md). The walkways are cut where the
  first network's segments meet, so that each piece lies along one reading. An OpenStreetMap
  ``width`` tag is used where there is one. Every other width is a default by kind of way: an
  assumption.
- The cost multipliers that make a crowd prefer plazas and footways to forest paths and parking
  aisles are those of ``geo/osm/build-skeleton.mjs``. A walkway known to be 5 m wide or more
  counts 10% shorter, whether the width is a tag or a reading.
- Venue exits are the first network's exit points. Station entrances are OpenStreetMap's; bus
  stops are the access registry's.
- Car parks are left out. The park's lots (P1, P2, P4 to P7) have no published coordinates, and
  OpenStreetMap numbers its car parks differently: its "주차장2" lies a kilometre from Olympic
  Hall, where the park's lot 2 is. Matching the two by number would send people to the wrong place.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re

from .paths import repo_of

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = "geo/osm/osm-walkways.json"
OUTPUT = "src/data/walkways.json"
WALK = ("footway", "path", "pedestrian", "steps", "living_street", "residential", "service", "cycleway", "track")
# How much longer a metre of each kind of way feels to a crowd leaving a venue (geo/osm/build-skeleton.mjs).
COST = {"pedestrian": 0.8, "footway": 1.0, "cycleway": 1.0, "service": 1.3, "living_street": 1.3, "residential": 1.5,
        "steps": 1.5, "path": 2.5, "track": 2.5}
UNPAVED = re.compile(r"^(dirt|ground|gravel|grass|earth|mud|sand|unpaved|fine_gravel|compacted|wood)$")
# Effective width in metres where nothing better is known. Assumptions, by kind of way.
WIDTH = {"plaza": 8.0, "walk": 3.0, "sidewalk": 2.5, "crossing": 4.0, "stairs": 3.0, "trail": 2.0, "road": 4.0, "aisle": 4.0}
# A walkway takes a first-network segment's width when every sample of it lies this close to that segment.
MATCH_METRES = 6.0
# The walkways are cut at the OpenStreetMap node this close to a point of the first network.
PIN_METRES = 3.0
# A crowd prefers a walkway known to be this wide: its length counts for less when ways are compared.
WIDE_METRES = 5.0
WIDE_COST = 0.9
STATION_REACH = 400.0
STOP_REACH = 80.0
EXIT_REACH = 40.0
# Venues without exit points in the first network: the building's OpenStreetMap name and the dashboard's venue.
# Each gets one exit at the walkway nearest to the building's outline. The real doors are not known: an assumption.
OTHER_VENUES = {"올림픽홀": ("olympic-hall", "올림픽홀"), "우리금융아트홀": ("woori-art-hall", "우리금융아트홀"),
                "SK올림픽핸드볼경기장": ("ticketlink-live-arena", "티켓링크 라이브 아레나")}
VENUE_REACH = 80.0
# A crowd leaves a building onto a walkway, not into a car park aisle or a roadway.
DOORSTEP_KINDS = ("plaza", "walk", "sidewalk", "crossing", "stairs")
EARTH = 6371000.0


def metres(a, b) -> float:
    """Great-circle distance between two [lng, lat] points."""
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2
    return 2 * EARTH * math.asin(math.sqrt(h))


def _flat(point, origin):
    """A point in metres east and north of ``origin``; exact enough over a park."""
    return ((point[0] - origin[0]) * math.cos(math.radians(origin[1])) * 111320.0, (point[1] - origin[1]) * 110540.0)


def to_line(point, line) -> float:
    """Distance in metres from a point to a polyline."""
    p = (0.0, 0.0)
    best = math.inf
    flat = [_flat(q, point) for q in line]
    for (ax, ay), (bx, by) in zip(flat, flat[1:]):
        dx, dy = bx - ax, by - ay
        span = dx * dx + dy * dy
        t = 0.0 if span == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / span))
        best = min(best, math.hypot(ax + t * dx - p[0], ay + t * dy - p[1]))
    return best if flat[1:] else math.hypot(*flat[0])


def samples(line, count: int = 5):
    """Points spread along a polyline, ends included."""
    lengths = [metres(a, b) for a, b in zip(line, line[1:])]
    total = sum(lengths)
    if total == 0:
        return [line[0]]
    out = []
    for i in range(count):
        target, walked = total * i / (count - 1), 0.0
        for (a, b), length in zip(zip(line, line[1:]), lengths):
            if walked + length >= target or (a, b) == (line[-2], line[-1]):
                t = 0.0 if length == 0 else min(1.0, (target - walked) / length)
                out.append([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t])
                break
            walked += length
    return out


def kind_of(tags: dict) -> str:
    """The kind of way the simulation and the map tell apart."""
    highway = tags["highway"]
    if highway == "steps":
        return "stairs"
    if highway == "pedestrian":
        return "plaza"
    if highway == "footway":
        return {"crossing": "crossing", "sidewalk": "sidewalk"}.get(tags.get("footway"), "walk")
    if highway in ("path", "track"):
        return "trail"
    if highway == "cycleway":
        return "walk"
    if highway == "service" and tags.get("service") in ("parking_aisle", "drive-through"):
        return "aisle"
    return "road"


def cost_of(tags: dict, known_width: float | None = None) -> float:
    """Route-choice multiplier of a way, as geo/osm/build-skeleton.mjs sets it.

    ``known_width`` is a width that was read or tagged, not a default.
    """
    cost = COST.get(tags["highway"], 2.0)
    if tags["highway"] == "service" and tags.get("service") in ("parking_aisle", "drive-through"):
        cost = 5.0
    if tags["highway"] == "service" and tags.get("service") == "driveway":
        cost = 1.6
    if UNPAVED.match(tags.get("surface", "")):
        cost *= 1.6
    if known_width is not None and known_width >= WIDE_METRES:
        cost *= WIDE_COST
    return round(cost, 3)


def tagged_width(tags: dict) -> float | None:
    match = re.match(r"^\s*(\d+(?:\.\d+)?)", str(tags.get("width", "")))
    if not match:
        return None
    width = float(match.group(1))
    return width if 0.5 <= width <= 40 else None


def walkable(element: dict) -> bool:
    tags = element.get("tags") or {}
    return (element["type"] == "way" and tags.get("highway") in WALK and tags.get("access") not in ("private", "no")
            and tags.get("foot") != "no" and len(element.get("nodes", [])) >= 2)


def segments(ways: list[dict], pins: set[int]) -> tuple[dict, list[dict]]:
    """Cut the ways where they meet. Returns node positions and the pieces between meeting points.

    A node is a meeting point when it ends a way, when more than one way passes through it,
    or when it is one of ``pins`` (a station entrance that sits in the middle of a way).
    """
    position, uses = {}, {}
    for way in ways:
        for node, point in zip(way["nodes"], way["geometry"]):
            position[node] = [point["lon"], point["lat"]]
            uses[node] = uses.get(node, 0) + 1
        for end in (way["nodes"][0], way["nodes"][-1]):
            uses[end] += 1                                           # an end always splits
    pieces = []
    for way in ways:
        start, part = 0, 0
        nodes = way["nodes"]
        for i in range(1, len(nodes)):
            if uses[nodes[i]] > 1 or nodes[i] in pins or i == len(nodes) - 1:
                chain = nodes[start:i + 1]
                if chain[0] != chain[-1]:
                    pieces.append({"id": f"w{way['id']}-{part}", "a": chain[0], "b": chain[-1], "tags": way["tags"],
                                   "path": [position[n] for n in chain]})
                    part += 1
                start = i
    return position, pieces


def nearest(point, candidates: dict) -> tuple[int | None, float]:
    best, gap = None, math.inf
    for node, where in candidates.items():
        d = metres(point, where)
        if d < gap:
            best, gap = node, d
    return best, gap


def reachable(pieces: list[dict], sources: list[int]) -> set[int]:
    """Nodes that can be walked to from any of ``sources``."""
    around = {}
    for piece in pieces:
        around.setdefault(piece["a"], []).append(piece["b"])
        around.setdefault(piece["b"], []).append(piece["a"])
    seen, queue = set(sources), list(sources)
    while queue:
        for other in around.get(queue.pop(), []):
            if other not in seen:
                seen.add(other)
                queue.append(other)
    return seen


def width_of(piece: dict, measured: list[dict]) -> tuple[float, str, str | None]:
    """Effective width of a piece: the aerial reading it runs along, an OpenStreetMap tag, or the default of its kind.

    A piece that runs along more than one reading takes the narrowest: that is what limits the flow.
    """
    points = samples(piece["path"])
    for segment in measured:
        if all(to_line(point, segment["path"]) <= MATCH_METRES for point in points):
            return float(segment["widthM"]), "판독", segment["id"]
    along = [min((s for s in measured if to_line(point, s["path"]) <= MATCH_METRES), key=lambda s: s["widthM"], default=None) for point in points]
    if all(along):
        narrowest = min(along, key=lambda s: s["widthM"])
        return float(narrowest["widthM"]), "판독", narrowest["id"]
    tagged = tagged_width(piece["tags"])
    if tagged is not None:
        return tagged, "OSM", None
    return WIDTH[kind_of(piece["tags"])], "가정", None



def build(extract: dict, network: dict, routes: dict, registry: dict) -> dict:
    """The walkway graph with the venue exits and the destinations a leaving crowd walks to."""
    elements = extract["elements"]
    entrances = [e for e in elements if e["type"] == "node" and (e.get("tags") or {}).get("railway") == "subway_entrance"]
    ways = [e for e in elements if walkable(e)]
    on_ways = {node: [point["lon"], point["lat"]] for way in ways for node, point in zip(way["nodes"], way["geometry"])}
    pins = {e["id"] for e in entrances if e["id"] in on_ways}
    # Cut the walkways where the first network's segments meet, so that a piece does not run along two readings.
    for node in network["nodes"]:
        at, gap = nearest(node["lngLat"], on_ways)
        if at is not None and gap <= PIN_METRES:
            pins.add(at)
    position, pieces = segments(ways, pins)

    # Keep what the venues' crowd can reach.
    ends = {node: position[node] for piece in pieces for node in (piece["a"], piece["b"])}
    exits = []
    for node in network["nodes"]:
        if node["kind"] != "exit":
            continue
        at, gap = nearest(node["lngLat"], ends)
        if at is not None and gap <= EXIT_REACH:
            exits.append({"id": node["id"], "name": node["name"], "venueId": node.get("venueId"), "share": node.get("exitShare"),
                          "nodeId": f"n{at}", "gapM": round(gap, 1), "at": at})
    if not exits:
        raise ValueError("No venue exit of the first network lies on a walkway of the extract")
    inside = reachable(pieces, [e["at"] for e in exits])
    pieces = [p for p in pieces if p["a"] in inside]
    ends = {node: where for node, where in ends.items() if node in inside}

    doorsteps = {node: ends[node] for piece in pieces if kind_of(piece["tags"]) in DOORSTEP_KINDS for node in (piece["a"], piece["b"])}
    for element in elements:
        venue = OTHER_VENUES.get((element.get("tags") or {}).get("name", "")) if element["type"] == "way" else None
        if not venue or not element.get("geometry"):
            continue
        ring = [[point["lon"], point["lat"]] for point in element["geometry"]]
        at, gap = min(((node, to_line(where, ring)) for node, where in doorsteps.items()), key=lambda pair: pair[1], default=(None, math.inf))
        if at is not None and gap <= VENUE_REACH:
            exits.append({"id": f"x-{venue[0]}", "name": f"{venue[1]} 출구", "venueId": venue[0], "share": 1, "nodeId": f"n{at}",
                          "gapM": round(gap, 1), "assumed": True, "at": at})

    measured = [e for e in network["edges"] if e.get("path") and e["type"] != "stairs"]
    edges = []
    for piece in pieces:
        width, source, base = width_of(piece, measured)
        length = sum(metres(a, b) for a, b in zip(piece["path"], piece["path"][1:]))
        edge = {"id": piece["id"], "a": f"n{piece['a']}", "b": f"n{piece['b']}", "lengthM": round(length, 1), "widthM": width,
                "widthSource": source, "kind": kind_of(piece["tags"]), "cost": cost_of(piece["tags"], None if source == "가정" else width)}
        if base:
            edge["baseEdgeId"] = base
        if len(piece["path"]) > 2:
            edge["path"] = [[round(x, 6), round(y, 6)] for x, y in piece["path"]]
        edges.append(edge)

    destinations = []
    by_node = {n["id"]: n for n in network["nodes"]}
    stations = {route["destinationNodeId"]: by_node[route["destinationNodeId"]]["lngLat"] for route in routes["routes"]}
    for route in routes["routes"]:
        first = by_node[route["destinationNodeId"]]
        doors = []
        for entrance in entrances:
            where = [entrance["lon"], entrance["lat"]]
            # An entrance belongs to the station whose known exit is nearest.
            station, reach = nearest(where, stations)
            if station != first["id"] or reach > STATION_REACH:
                continue
            at, gap = (entrance["id"], 0.0) if entrance["id"] in ends else nearest(where, ends)
            if at is not None and gap <= 30:
                doors.append({"ref": (entrance.get("tags") or {}).get("ref"), "nodeId": f"n{at}", "lngLat": [round(where[0], 6), round(where[1], 6)]})
        doors.sort(key=lambda door: (str(door["ref"]), door["nodeId"]))
        if doors:
            destinations.append({"id": first["id"], "mode": "rail", "direction": route["direction"],
                                 "name": re.sub(r"\s*\d+번 출구$", "", route["name"]), "line": route.get("line"),
                                 "lngLat": first["lngLat"], "nodeIds": sorted({door["nodeId"] for door in doors}), "entrances": doors})
    for place in registry["destinations"]:
        if place["firstMode"] != "bus":
            continue
        where = place.get("approachLngLat") or place.get("sourceLngLat") or place.get("lngLat")
        at, gap = nearest(where, ends) if where else (None, math.inf)
        if at is not None and gap <= STOP_REACH:
            destinations.append({"id": place["id"], "mode": "bus", "name": place["name"], "lngLat": [round(where[0], 6), round(where[1], 6)],
                                 "nodeIds": [f"n{at}"], "gapM": round(gap, 1)})

    used = {edge[end] for edge in edges for end in ("a", "b")}
    nodes = [{"id": f"n{node}", "lngLat": [round(where[0], 6), round(where[1], 6)]} for node, where in sorted(ends.items()) if f"n{node}" in used]
    return {
        "schemaVersion": 1,
        "source": "© OpenStreetMap contributors, ODbL 1.0",
        "osmBase": extract.get("osm3s", {}).get("timestamp_osm_base"),
        "builtBy": "ml/olpark_demand/walkways.py",
        "widthDefaultsM": WIDTH,
        "nodes": nodes,
        "edges": edges,
        "exits": [{key: value for key, value in e.items() if key != "at"} for e in exits],
        "destinations": destinations,
    }


def summary(graph: dict) -> dict:
    sources = {}
    for edge in graph["edges"]:
        sources[edge["widthSource"]] = sources.get(edge["widthSource"], 0) + 1
    modes = {}
    for place in graph["destinations"]:
        modes[place["mode"]] = modes.get(place["mode"], 0) + 1
    return {"nodes": len(graph["nodes"]), "edges": len(graph["edges"]),
            "kilometres": round(sum(e["lengthM"] for e in graph["edges"]) / 1000, 1), "widthSources": sources,
            "exits": [e["id"] for e in graph["exits"]], "destinations": modes, "osmBase": graph["osmBase"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--extract", type=Path, help=f"OpenStreetMap extract (default: {EXTRACT} at the repository root)")
    parser.add_argument("--output", type=Path, help=f"where to write the graph (default: {OUTPUT})")
    args = parser.parse_args()
    repo = repo_of(args.root)
    read = lambda path: json.loads((repo / path).read_text(encoding="utf-8"))
    extract = json.loads((args.extract or repo / EXTRACT).read_text(encoding="utf-8"))
    graph = build(extract, read("src/data/network.json"), read("src/data/routes.json"), read("src/data/egress-access.json"))
    output = args.output or repo / OUTPUT
    output.write_text(json.dumps(graph, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps(summary(graph) | {"file": str(output), "bytes": output.stat().st_size}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
