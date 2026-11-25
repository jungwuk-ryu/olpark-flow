"""Read the frozen KSPO pedestrian access catalogue (standard library only)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import math
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "src/data"
NETWORK_REVISION = "48e50c3614e2b8786062278ede9c2b822ea6f4b7"
PINNED_BASE_SHA256 = {
    "network.json": "e24efce18e3fd7d0be6a1644b9149c520c9d00eef3e00f68b43d289aeb9f951c",
    "routes.json": "97af91cd34855946a40dbfd7bec8b59e533d1c07c52fc55d81d631eb4e6a4eab",
    "parking.json": "787d08626da02dc72206744aa2720be7d16be1e2943fa0a509627c2903081663",
}
ROUTABLE = frozenset({"base_graph", "modelled_connector", "field_verified"})
MODES = {"rail", "bus", "parked_vehicle", "taxi_pickup", "walk_only"}


def mapping_version(packet):
    payload = {name: value for name, value in packet.items() if name != "mappingVersion"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "egress-access-v1-" + sha256(encoded.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class NetworkRegistry:
    mapping_version: str
    network_revision: str
    published_at: datetime
    path_elements: dict[tuple[str, str], frozenset[str]]
    destination_modes: dict[str, str]
    origin_venues: dict[str, str]
    destinations: dict[str, dict]
    element_ids: frozenset[str]


def _by_id(items):
    result = {item["id"]: item for item in items}
    if len(result) != len(items):
        raise ValueError("Duplicate network element identifier")
    return result


def load_registry(data_dir=DATA_DIR, *, mapping_path=None):
    """Load and check the immutable asset, path directions and the three base graph files."""
    data_dir = Path(data_dir)
    overlay = Path(mapping_path) if mapping_path is not None else data_dir / "egress-access.json"
    packet = json.loads(overlay.read_bytes())
    if (packet["schemaVersion"] != 1 or packet["networkRevision"] != NETWORK_REVISION
            or packet["mappingVersion"] != mapping_version(packet)):
        raise ValueError("Access catalogue or pinned graph version mismatch")
    if packet["baseFileSha256"] != PINNED_BASE_SHA256:
        raise ValueError("Base hashes do not match the network revision")
    base = {}
    for name in PINNED_BASE_SHA256:
        asset = (data_dir / name).read_bytes()
        if sha256(asset).hexdigest() != packet["baseFileSha256"][name]:
            raise ValueError(f"Base graph content mismatch: {name}")
        base[name] = json.loads(asset)
    nodes = _by_id(base["network.json"]["nodes"] + packet["nodes"])
    edges = _by_id(base["network.json"]["edges"] + packet["edges"])
    if nodes.keys() & edges.keys():
        raise ValueError("Node and edge IDs must be globally unique")
    origins = {key: n["venueId"] for key, n in nodes.items() if n["kind"] == "exit"}
    destinations = _by_id(packet["destinations"])
    if any(d["firstMode"] not in MODES or d["status"] not in ROUTABLE | {"candidate_only", "unmapped"}
           or (d["status"] in ROUTABLE and d["id"] not in nodes) for d in destinations.values()):
        raise ValueError("Invalid destination catalogue entry")
    for e in edges.values():
        if e["from"] not in nodes or e["to"] not in nodes or e["from"] == e["to"]:
            raise ValueError(f"Invalid network edge: {e['id']}")
        if "capacityEdgeId" in e:
            shared = edges.get(e["capacityEdgeId"])
            if (not shared or "capacityEdgeId" in shared or e["from"] != shared["from"]
                    or not all(math.isfinite(value) for value in (e["lengthM"], shared["lengthM"]))
                    or not 0 < e["lengthM"] <= shared["lengthM"]
                    or e["widthM"] != shared["widthM"] or e["queueAreaM2"] != shared["queueAreaM2"]
                    or e["type"] != shared["type"]):
                raise ValueError(f"Invalid shared entrance capacity: {e['id']}")
    # Either a partial edge or its parent denotes the same physical entrance
    # closure. Expose the same result to consumers checking sets of element IDs.
    aliases = defaultdict(set)
    for eid, edge in edges.items():
        aliases[edge.get("capacityEdgeId", eid)].add(eid)
    paths = {}
    ordered = {}
    for path in packet["paths"]:
        origin, dest, ids = path["exitNodeId"], path["destinationId"], path["edgeIds"]
        if ((origin, dest) in paths or origin not in origins or dest not in destinations
                or destinations[dest]["status"] not in ROUTABLE or not ids):
            raise ValueError(f"Invalid origin/destination mapping: {origin}, {dest}")
        at = origin
        seen = {at}
        shared_entrances = set()
        for eid in ids:
            edge = edges.get(eid)
            if not edge or edge["from"] != at or edge["to"] in seen:
                raise ValueError(f"Disconnected, reversed or cyclic access path: {eid}")
            resource = edge.get("capacityEdgeId", eid)
            if resource in shared_entrances:
                raise ValueError(f"A path reuses the same physical entrance: {resource}")
            shared_entrances.add(resource)
            at = edge["to"]
            seen.add(at)
            seen.update(aliases[resource])
        if at != dest:
            raise ValueError(f"Access path does not reach {dest}")
        paths[origin, dest] = frozenset(seen)
        ordered[origin, dest] = ids
    for route in base["routes.json"]["routes"]:
        for path in route["paths"]:
            if ordered.get((path["exitNodeId"], route["destinationNodeId"])) != path["edgeIds"]:
                raise ValueError("A pinned Team B rail path is missing or changed")
    if {dest for _, dest in paths} != {d["id"] for d in destinations.values() if d["status"] in ROUTABLE}:
        raise ValueError("Routable destination lacks access path")
    published = datetime.fromisoformat(packet["publishedAt"])
    if published.utcoffset() is None:
        raise ValueError("Access publication requires a timezone")
    return NetworkRegistry(packet["mappingVersion"], packet["networkRevision"], published, paths,
                           {k: d["firstMode"] for k, d in destinations.items()}, origins,
                           destinations, frozenset(nodes) | frozenset(edges))
