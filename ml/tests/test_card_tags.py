import io
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

import pandas as pd

from olpark_demand import collect_card_tags as tags

DAY = "2026-08-01"
HEADER = "TRCR_ID,RIDE_DTM,SERIAL_NUM,TRNC_ID,TRNS_MNS_CD,TRTR_FCNT,ROUTE_ID,TRCM_ID,VHCL_ID,USER_DVS_CD,TRIP_DEPR_DTM,RIDE_BSST_ID,ALGH_DTM,ALGH_BSST_ID,PSNG_NUM,RIDE_AMT,ALGH_AMT,TRIP_DEPR_DTM_H,RIDE_DTM_H,ALGH_DTM_H,STDR_DE"
NEAR, AREA = {"123000154"}, {"123000154", "123000999"}


def leg(card, mode, ride_at, ride_time, alight_at, alight_time, persons=1, service="20260801", fare="01"):
    return f"{card},{ride_time},1,1,{mode},0,r,c,v,{fare},{ride_time},{ride_at},{alight_time},{alight_at},{persons},0,0,0,0,0,{service}"


def archive(folder: Path, rows: list[str]) -> Path:
    path = folder / "TAIMS_20260805_20260801.zip"
    with ZipFile(path, "w") as out:
        out.writestr("VW_KSCC_DX_CARD.csv", "﻿" + "\n".join([HEADER] + rows) + "\n")
        for product in ("DC", "DD"):
            out.writestr(f"VW_KSCC_{product}_CARD.csv", "﻿" + HEADER + "\n")
    return path


class CardTagTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        rows = [
            # A visitor: off the train at the park at 16:02, back on at 21:07 with a companion on the same card.
            leg("A", "205", "4125", "20260801153000", "4136", "20260801160200"),
            leg("A", "205", "4136", "20260801210700", "4125", "20260801214000", persons=2),
            # A transfer: off the bus by the park and straight onto a train is not a visit.
            leg("B", "115", "123000001", "20260801180000", "123000154", "20260801181000"),
            leg("B", "203", "2556", "20260801181500", "2511", "20260801190000"),
            # After midnight on the same service date, and a row of another service date.
            leg("C", "205", "4136", "20260802001200", "4101", "20260802010000"),
            leg("D", "205", "4136", "20260801090000", "4101", "20260801100000", service="20260731"),
            # A stop of the wider area, an unusable alighting time, and a place that is not of interest.
            leg("E", "120", "123000999", "20260801070100", "100000001", "00"),
            leg("F", "203", "2511", "20260801070000", "2512", "20260801071000"),
        ]
        self.tables, self.audit = tags.aggregate(archive(self.folder, rows), DAY, NEAR, AREA)

    def test_counts_by_station_direction_and_five_minutes(self):
        rail = self.tables["rail_5min"].set_index(["site", "direction", "minute"]).persons
        self.assertEqual(rail[("9:4136", "off", 16 * 60)], 1)
        self.assertEqual(rail[("9:4136", "on", 21 * 60 + 5)], 2)
        self.assertEqual(rail[("5:2556", "on", 18 * 60 + 15)], 1)
        self.assertEqual(rail[("9:4136", "on", 24 * 60 + 10)], 1)       # after midnight stays with the service date
        self.assertEqual(int(rail.sum()), 5)                             # the other service date and other stations are out
        bus = self.tables["bus_hourly"].set_index(["stop_id", "direction", "hour"]).persons
        self.assertEqual(bus[("123000154", "off", 18)], 1)
        self.assertEqual(bus[("123000999", "on", 7)], 1)
        self.assertEqual(set(self.tables["bus_5min"].stop_id), {"123000154"})
        self.assertEqual(self.audit["products"]["DX"]["rows_of_another_service_date"], 1)

    def test_a_return_needs_an_earlier_arrival_and_a_stay(self):
        returns = self.tables["returns"]
        self.assertEqual(len(returns), 1)
        row = returns.iloc[0]
        self.assertEqual((row.arrived_at, row.left_from, row.stay, row.minute, row.persons), ("9:4136", "9:4136", "4-6h", 1265, 2))
        arrivals = self.tables["arrivals"].set_index(["place", "left_from"]).persons
        self.assertEqual(arrivals[("9:4136", "9:4136")], 1)
        self.assertEqual(arrivals[("bus:near", "none")], 1)             # five minutes is a transfer, not a stay

    def test_no_output_carries_a_card_number(self):
        for frame in self.tables.values():
            self.assertFalse({"TRCR_ID", "card"} & set(frame.columns))
        self.assertNotIn("TRCR_ID", str(self.audit))

    def test_hourly_layout_fills_silent_hours_with_zero(self):
        root = self.folder / "root"
        (root / "data/prepared/card_tags").mkdir(parents=True)
        self.tables["rail_5min"].to_parquet(root / "data/prepared/card_tags/rail_5min.parquet", index=False)
        hourly = tags.hourly(root)
        self.assertEqual(set(hourly.hour), set(range(6, 25)))
        on = hourly[hourly.direction.eq("on") & hourly.site.eq("9:4136")].set_index("hour").persons
        self.assertEqual((on[21], on[24], on[10]), (2, 1, 0))
        self.assertEqual(len(hourly), hourly.site.nunique() * 2 * 19)


if __name__ == "__main__":
    unittest.main()
