"""역전 시각만 제외하고 다른 조건을 추가하지 않는지 확인한다."""
import contextlib
import csv
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest

from parking_2025 import main as parking_main
from vehicle_time_audit import filter_reversed


class VehicleTimeAuditTest(unittest.TestCase):
    def test_only_reversed_times_are_removed(self):
        rows = [
            ['1', '북2문입구#1', '2025-01-10 22:00:00', '북2문출구#1', '2025-01-10 21:00:00'],
            ['2', '북2문입구#1', '2025-01-10 21:00:00', '북2문출구#1', '2025-01-10 21:00:00'],
            ['3', '동2문입구#1', '2025-01-09 22:00:00', '동2문CMB', '2025-01-10 21:00:00'],
            ['4', '남2문입구#1', '2025-12-31 22:00:00', '남2문출구#1', '2026-01-01 01:00:00'],
            ['5', '알수없는입구', '해석불가', '사전정산기#19', '2025-01-10 21:00:00'],
        ]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source, target = folder / 'raw.csv.gz', folder / 'filtered.csv.gz'
            with gzip.open(source, 'wt', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['id', 'entry_gate', 'entry_time', 'exit_gate', 'exit_time'])
                writer.writerows(rows)
            result = filter_reversed(source, target)
            with gzip.open(target, 'rt', encoding='utf-8-sig', newline='') as stream:
                kept = list(csv.DictReader(stream))
            self.assertEqual([row['id'] for row in kept], ['2', '3', '4', '5'])
            self.assertEqual(result['counts']['input_rows'], 5)
            self.assertEqual(result['counts']['kept_rows'], 4)
            self.assertEqual(result['counts']['excluded_rows'], 1)
            self.assertEqual(result['counts']['unparsed_times_kept'], 1)
            self.assertEqual(result['excluded_by_exit_year'], {'2025': 1})
            self.assertEqual(result['counts']['excluded_event_16_24'], 1)

    def test_json_output_preserves_throughput_computation(self):
        # 같은 분에 출차 60대를 두면 10분 최대 평균은 6대/분이다.
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source, output = folder / 'sample.csv.gz', folder / 'result.json'
            with gzip.open(source, 'wt', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['id', 'entry_gate', 'entry_time', 'exit_gate', 'exit_time'])
                for i in range(60):
                    writer.writerow([str(i), '북2문입구#1', '2025-01-10 18:00:00',
                                     '북2문출구#1', '2025-01-10 21:00:00'])
            with contextlib.redirect_stdout(io.StringIO()):
                values, _ = parking_main(str(source), str(output))
            result = json.loads(output.read_text())
            self.assertEqual(values['gate-north-2'], [6])
            self.assertEqual(result['throughput']['gate-north-2']['p90'], 6)
            self.assertEqual(result['rows'], 60)
            self.assertEqual(result['excess']['days'], 0)


if __name__ == '__main__':
    unittest.main()
