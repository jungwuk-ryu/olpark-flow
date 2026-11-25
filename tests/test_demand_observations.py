import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from olpark_demand.observations import (
    BUS_DAY_FILE, BUS_HOUR_FILE, OBSERVATION_FILES, PARKING_FILE, RAIL_FILE, load_observations,
)


class ObservationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        rail = pd.DataFrame([
            ("D03", "2025-01-01", "5", "2556", "승차", "06-07시간대", "hour", 0),
            ("D03", "2025-01-01", "5", "2556", "하차", "06-07시간대", "hour", 2),
            ("D03", "2025-01-01", "5", "2556", "승차", "06시이전", "open_interval", 10),
            ("D04", "2025-01-01", "9", "4136", "순승차", "06시-07시", "hour", 22),
        ], columns=["dataset_id", "service_date", "line", "station_id", "flow", "interval_label", "interval_type",
                    "persons"])
        rail.to_csv(self.data / RAIL_FILE, index=False)

        parking = []
        # 2024-12-31 21:00 UTC is 2025-01-01 06:00 in Seoul.
        for utc, counts in (("2024-12-31 21:00", [2, 3, 1]), ("2024-12-31 22:00", [1, 0, 2]),
                            ("2024-12-31 23:00", [1, 3]), ("2025-07-30 21:00", [1, 1, 1]),
                            ("2025-12-01 11:00", [4, 5, 6])):
            for gate, n in zip(("동2", "남2", "북2"), counts):
                parking.append((pd.Timestamp(utc, tz="UTC"), gate, "OUT", n))
        pd.DataFrame(parking, columns=["hour_kst", "gate", "movement", "vehicles"]).to_parquet(self.data / PARKING_FILE)

        daily = pd.DataFrame([
            ("2025-01-01", "24123", "123000034", "N31", "N31강동", "표지(25)", 3),
            ("2025-01-01", "24123", "123000034", "N31", "N31정릉", "표지(95)", 1),
            ("2025-01-02", "24123", "123000034", "N31", "N31강동", "표지(25)", 8),
            ("2025-01-02", "24123", "123000034", "N31", "N31정릉", "표지(95)", None),
            # Changed route roster; the new explicit zero remains a valid station-day count.
            ("2025-01-03", "24123", "123000034", "340", "340", "표지(25)", 0),
        ], columns=["service_date", "ars_id", "stop_id", "route_number", "route_name", "stop_name", "boardings"])
        daily["source_registration_date"] = ["20250103", "20250104", "20250105", "20250106", None]
        daily.to_parquet(self.data / BUS_DAY_FILE)

        hourly = pd.DataFrame([
            ("2025-01-01", 6, "24123", "8002350", 0), ("2025-01-01", 7, "24123", "8002350", None),
            ("2025-01-01", 3, "24123", "8002350", 5),
        ], columns=["service_date", "source_hour", "ars_id", "source_stop_id", "boardings"])
        hourly.to_parquet(self.data / BUS_HOUR_FILE)

    def test_independent_units_zeros_and_missing_observations(self):
        frame, quality = load_observations(self.data)
        totals = frame.groupby("mode").y.sum().to_dict()
        self.assertEqual(totals, {"rail": 22, "parking": 6, "bus_day": 4, "bus_hour": 0})
        self.assertEqual(frame.groupby("mode").size().to_dict(), {"rail": 2, "parking": 3, "bus_day": 2, "bus_hour": 1})
        park = frame[frame["mode"].eq("parking")]
        self.assertEqual(set(zip(park.date, park.hour)), {("2025-01-01", 6)})
        bus = frame[frame["mode"].eq("bus_day")]
        self.assertEqual(bus[["date", "hour", "y"]].to_dict("split")["data"],
                         [["2025-01-01", -1, 4], ["2025-01-03", -1, 0]])
        self.assertEqual(bus.loc[bus.date.eq("2025-01-01"), "label_available_on"].item(), "2025-01-05")
        self.assertTrue(bus.loc[bus.date.eq("2025-01-03"), "label_available_on"].isna().all())
        self.assertTrue(frame.loc[frame["mode"].ne("bus_day"), "label_available_on"].isna().all())
        self.assertEqual(quality["modes"]["bus_day"]["excluded_stop_days_with_incomplete_rows"], 1)
        self.assertEqual(len(quality["modes"]["bus_day"]["observed_route_profiles_by_site"]["24123"]), 2)
        self.assertEqual(quality["modes"]["bus_hour"]["zero_rows"], 1)
        self.assertEqual(quality["modes"]["rail"]["zero_rows"], 1)
        json.dumps(quality, allow_nan=False)

    def test_reject_duplicate_source_variant(self):
        path = self.data / BUS_DAY_FILE
        original = pd.read_parquet(path)
        pd.concat([original, original.iloc[:1]], ignore_index=True).to_parquet(path)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            load_observations(self.data)

    def test_reject_changed_stop_identity(self):
        path = self.data / BUS_HOUR_FILE
        data = pd.read_parquet(path)
        data.loc[0, "source_stop_id"] = "0045678"
        data.to_parquet(path)
        with self.assertRaisesRegex(ValueError, "changed"):
            load_observations(self.data)


class ArchiveTests(unittest.TestCase):
    def test_current_archive_counts(self):
        data = Path(__file__).resolve().parents[1] / "data" / "prepared"
        if not all((data / file).is_file() for file in OBSERVATION_FILES):
            self.skipTest("2025 local prepared archive is unavailable")
        frame, quality = load_observations(data)
        expected = {"rail": (26280, 9042938), "parking": (17001, 2457696),
                    "bus_day": (5110, 2560448), "bus_hour": (10332, 246230)}
        for mode, (rows, total) in expected.items():
            subset = frame[frame["mode"].eq(mode)]
            self.assertEqual((len(subset), subset.y.sum()), (rows, total))
        self.assertEqual(len(frame.drop_duplicates(["mode", "site", "date", "hour"])), len(frame))
        self.assertEqual(quality["modes"]["bus_hour"]["zero_rows"], 347)
        self.assertEqual(quality["modes"]["bus_hour"]["site_coverage"]["24211"]["days"], 28)
        daily = frame[frame["mode"].eq("bus_day")]
        lag = pd.to_datetime(daily.label_available_on) - pd.to_datetime(daily.date)
        self.assertTrue(lag.dt.days.eq(4).all())


if __name__ == "__main__":
    unittest.main()
