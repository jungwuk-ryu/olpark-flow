import unittest

from olpark_demand import walkways

# A small park. One degree of latitude is about 111 km, so 0.0005 is about 55 m.
#
#   7 --- 8            (cut off from the rest)
#
#   4
#   | steps
#   1 --- 2 - 9 - 3 === 6 (station entrance)
#   exit          |  plaza
#                 5      (private)
#
# Node 9 is a point of the first network where a wide reading ends and a narrow one begins.
AT = {1: (127.1200, 37.5200), 2: (127.1210, 37.5200), 3: (127.1220, 37.5200), 4: (127.1210, 37.5205),
      5: (127.1220, 37.5195), 6: (127.1230, 37.5200), 7: (127.1200, 37.5230), 8: (127.1210, 37.5230),
      9: (127.1215, 37.5200)}


def way(identity, nodes, **tags):
    return {"type": "way", "id": identity, "nodes": nodes, "geometry": [{"lon": AT[n][0], "lat": AT[n][1]} for n in nodes], "tags": tags}


EXTRACT = {"osm3s": {"timestamp_osm_base": "2026-11-25T18:37:11Z"}, "elements": [
    way(1, [1, 2, 9, 3], highway="footway"),
    way(2, [2, 4], highway="steps"),
    way(3, [3, 5], highway="footway", access="private"),
    way(4, [3, 6], highway="pedestrian", width="12 m"),
    way(5, [7, 8], highway="footway"),
    {"type": "node", "id": 6, "lon": AT[6][0], "lat": AT[6][1], "tags": {"railway": "subway_entrance", "ref": "3"}},
    {"type": "way", "id": 9, "nodes": [], "tags": {"amenity": "parking", "name": "올림픽공원 주차장2"},
     "geometry": [{"lon": 127.1219, "lat": 37.5203}, {"lon": 127.1221, "lat": 37.5203}, {"lon": 127.1221, "lat": 37.5205}]},
    {"type": "way", "id": 10, "nodes": [], "tags": {"amenity": "parking", "name": "방이공영주차장"},
     "geometry": [{"lon": 127.1219, "lat": 37.5203}, {"lon": 127.1221, "lat": 37.5203}]},
    # A hall whose outline runs just north of node 2, and one too far from any walkway.
    {"type": "way", "id": 11, "nodes": [], "tags": {"building": "yes", "name": "올림픽홀"},
     "geometry": [{"lon": 127.1208, "lat": 37.5202}, {"lon": 127.1212, "lat": 37.5202}, {"lon": 127.1212, "lat": 37.5204}]},
    {"type": "way", "id": 12, "nodes": [], "tags": {"building": "yes", "name": "우리금융아트홀"},
     "geometry": [{"lon": 127.1300, "lat": 37.5300}, {"lon": 127.1302, "lat": 37.5300}]},
]}
NETWORK = {
    "nodes": [{"id": "n-exit", "kind": "exit", "name": "남측 출구", "venueId": "kspo-dome", "exitShare": 1, "lngLat": [127.12001, 37.52001]},
              {"id": "n-dest-a", "kind": "destination", "name": "올림픽공원역 3번 출구", "direction": "A", "lngLat": [127.12301, 37.52001]},
              {"id": "n-junction-1", "kind": "junction", "name": "분기점 1", "lngLat": [127.12151, 37.52001]}],
    "edges": [{"id": "e-01", "type": "flat", "widthM": 5.9, "path": [[127.1200, 37.5200], [127.1215, 37.5200]]},
              {"id": "e-02", "type": "flat", "widthM": 4.4, "path": [[127.1215, 37.5200], [127.1220, 37.5200]]},
              {"id": "e-station-a", "type": "stairs", "widthM": 3.0, "path": [[127.1210, 37.5200], [127.1210, 37.5205]]}],
}
ROUTES = {"routes": [{"direction": "A", "name": "올림픽공원역 3번 출구", "line": "5·9호선", "destinationNodeId": "n-dest-a"}]}
REGISTRY = {"destinations": [
    {"id": "bus-1", "firstMode": "bus", "name": "공원 앞 (1)", "approachLngLat": [127.12102, 37.52052]},
    {"id": "bus-far", "firstMode": "bus", "name": "먼 정류장 (2)", "approachLngLat": [127.1300, 37.5300]},
    {"id": "park-lot-p1", "firstMode": "parked_vehicle", "name": "1주차장"},
]}


class GeometryTests(unittest.TestCase):
    def test_distance_between_two_points(self):
        self.assertAlmostEqual(walkways.metres([127.12, 37.52], [127.12, 37.521]), 111.2, delta=0.3)
        self.assertAlmostEqual(walkways.metres([127.12, 37.52], [127.121, 37.52]), 88.2, delta=0.3)

    def test_distance_from_a_point_to_a_line(self):
        line = [[127.1200, 37.5200], [127.1220, 37.5200]]
        self.assertAlmostEqual(walkways.to_line([127.1210, 37.5201], line), 11.05, delta=0.1)
        # Beyond the end of the line the nearest point is the end.
        self.assertAlmostEqual(walkways.to_line([127.1230, 37.5200], line), 88.2, delta=0.5)

    def test_samples_run_from_one_end_to_the_other(self):
        points = walkways.samples([[127.1200, 37.5200], [127.1210, 37.5200], [127.1220, 37.5200]], 5)
        self.assertEqual([round(p[0], 4) for p in points], [127.12, 127.1205, 127.121, 127.1215, 127.122])


class TagTests(unittest.TestCase):
    def test_kinds_the_map_tells_apart(self):
        kind = lambda **tags: walkways.kind_of(tags)
        self.assertEqual(kind(highway="steps"), "stairs")
        self.assertEqual(kind(highway="pedestrian"), "plaza")
        self.assertEqual(kind(highway="footway", footway="crossing"), "crossing")
        self.assertEqual(kind(highway="footway", footway="sidewalk"), "sidewalk")
        self.assertEqual(kind(highway="cycleway"), "walk")
        self.assertEqual(kind(highway="path"), "trail")
        self.assertEqual(kind(highway="service", service="parking_aisle"), "aisle")
        self.assertEqual(kind(highway="residential"), "road")

    def test_route_cost_follows_the_first_network_script(self):
        self.assertEqual(walkways.cost_of({"highway": "pedestrian"}), 0.8)
        self.assertEqual(walkways.cost_of({"highway": "service", "service": "parking_aisle"}), 5.0)
        self.assertEqual(walkways.cost_of({"highway": "service", "service": "driveway"}), 1.6)
        self.assertEqual(walkways.cost_of({"highway": "path", "surface": "dirt"}), 4.0)

    def test_a_walkway_known_to_be_wide_counts_shorter(self):
        self.assertEqual(walkways.cost_of({"highway": "footway"}, 6.0), 0.9)
        self.assertEqual(walkways.cost_of({"highway": "footway"}, 4.9), 1.0)
        # The builder passes the width it settled on. A tag alone, or a default, changes nothing.
        self.assertEqual(walkways.cost_of({"highway": "footway", "width": "6"}), 1.0)

    def test_width_tag_is_read_when_it_is_a_plausible_number(self):
        self.assertEqual(walkways.tagged_width({"width": "3"}), 3.0)
        self.assertEqual(walkways.tagged_width({"width": "12 m"}), 12.0)
        self.assertIsNone(walkways.tagged_width({"width": "narrow"}))
        self.assertIsNone(walkways.tagged_width({"width": "0.2"}))
        self.assertIsNone(walkways.tagged_width({}))


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = walkways.build(EXTRACT, NETWORK, ROUTES, REGISTRY)
        cls.edges = {e["id"]: e for e in cls.graph["edges"]}

    def test_ways_are_cut_where_they_meet_and_unusable_ones_dropped(self):
        # The footway is cut at node 2 where the steps leave it; the private way and the cut-off way are gone.
        self.assertEqual(sorted(self.edges), ["w1-0", "w1-1", "w1-2", "w2-0", "w4-0"])
        self.assertEqual((self.edges["w1-0"]["a"], self.edges["w1-0"]["b"]), ("n1", "n2"))
        self.assertEqual(sorted(n["id"] for n in self.graph["nodes"]), ["n1", "n2", "n3", "n4", "n6", "n9"])
        self.assertAlmostEqual(self.edges["w1-0"]["lengthM"], 88.2, delta=0.3)

    def test_ways_are_cut_where_the_first_network_changes_width(self):
        # No other way meets the footway at node 9. It is cut there because a point of the first network sits on it.
        self.assertEqual((self.edges["w1-1"]["a"], self.edges["w1-1"]["b"]), ("n2", "n9"))
        self.assertEqual((self.edges["w1-1"]["widthM"], self.edges["w1-1"]["baseEdgeId"]), (5.9, "e-01"))
        self.assertEqual((self.edges["w1-2"]["widthM"], self.edges["w1-2"]["baseEdgeId"]), (4.4, "e-02"))
        # The wide part is preferred when ways are compared; the narrow part is not.
        self.assertEqual((self.edges["w1-1"]["cost"], self.edges["w1-2"]["cost"]), (0.9, 1.0))

    def test_a_piece_along_two_readings_takes_the_narrowest(self):
        piece = {"path": [list(AT[1]), list(AT[3])], "tags": {"highway": "footway"}}
        measured = [e for e in NETWORK["edges"] if e["type"] != "stairs"]
        self.assertEqual(walkways.width_of(piece, measured), (4.4, "판독", "e-02"))
        # Half of this piece lies off every reading: the default stands.
        beyond = {"path": [list(AT[9]), list(AT[6])], "tags": {"highway": "footway"}}
        self.assertEqual(walkways.width_of(beyond, measured), (3.0, "가정", None))

    def test_width_comes_from_the_aerial_reading_then_the_tag_then_the_default(self):
        self.assertEqual((self.edges["w1-0"]["widthM"], self.edges["w1-0"]["widthSource"], self.edges["w1-0"]["baseEdgeId"]), (5.9, "판독", "e-01"))
        self.assertEqual((self.edges["w4-0"]["widthM"], self.edges["w4-0"]["widthSource"]), (12.0, "OSM"))
        # The steps lie along the first network's assumed station stairs, which carry no reading.
        self.assertEqual((self.edges["w2-0"]["widthM"], self.edges["w2-0"]["widthSource"]), (3.0, "가정"))
        self.assertNotIn("baseEdgeId", self.edges["w2-0"])
        self.assertEqual((self.edges["w2-0"]["kind"], self.edges["w4-0"]["kind"]), ("stairs", "plaza"))

    def test_exit_station_and_stop_sit_on_the_graph(self):
        exits = {e["id"]: e for e in self.graph["exits"]}
        self.assertEqual((exits["n-exit"]["nodeId"], exits["n-exit"]["share"]), ("n1", 1))
        self.assertNotIn("assumed", exits["n-exit"])
        places = {d["id"]: d for d in self.graph["destinations"]}
        self.assertEqual(sorted(places), ["bus-1", "n-dest-a"])
        station = places["n-dest-a"]
        self.assertEqual((station["mode"], station["direction"], station["name"], station["line"], station["nodeIds"]),
                         ("rail", "A", "올림픽공원역", "5·9호선", ["n6"]))
        self.assertEqual(station["entrances"][0]["ref"], "3")
        self.assertEqual((places["bus-1"]["mode"], places["bus-1"]["nodeIds"]), ("bus", ["n4"]))

    def test_car_parks_are_left_out(self):
        # OpenStreetMap's "주차장2" is not the park's lot 2: the two number their car parks differently.
        self.assertFalse([d for d in self.graph["destinations"] if d["mode"] == "car"])

    def test_a_venue_without_exit_points_gets_one_assumed_exit_at_the_nearest_walkway(self):
        exits = {e["id"]: e for e in self.graph["exits"]}
        self.assertEqual(sorted(exits), ["n-exit", "x-olympic-hall"])
        hall = exits["x-olympic-hall"]
        # The outline's edge passes 22 m north of node 2; the steps to node 4 make node 4 just as near.
        self.assertIn(hall["nodeId"], ("n2", "n4"))
        self.assertEqual((hall["venueId"], hall["name"], hall["share"], hall["assumed"]), ("olympic-hall", "올림픽홀 출구", 1, True))
        self.assertLess(hall["gapM"], 30)

    def test_the_graph_names_its_source_and_assumptions(self):
        self.assertIn("OpenStreetMap", self.graph["source"])
        self.assertEqual(self.graph["osmBase"], "2026-11-25T18:37:11Z")
        self.assertEqual(self.graph["widthDefaultsM"]["stairs"], 3.0)
        self.assertEqual(walkways.summary(self.graph)["widthSources"], {"판독": 3, "가정": 1, "OSM": 1})

    def test_an_extract_without_the_venue_is_refused(self):
        far = {**NETWORK, "nodes": [{**NETWORK["nodes"][0], "lngLat": [127.2, 37.6]}, NETWORK["nodes"][1]]}
        with self.assertRaises(ValueError):
            walkways.build(EXTRACT, far, ROUTES, REGISTRY)


if __name__ == "__main__":
    unittest.main()
