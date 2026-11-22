"""시각 이상·특수 차로·공연일·같은 날 입차 조건의 분리 검증."""
import contextlib
import csv
import gzip
import io
from pathlib import Path
import tempfile
import unittest

from parking_exit_check import analyze


class ParkingExitCheckTest(unittest.TestCase):
    def test_scope_and_exclusions(self):
        # 2025-01-10은 날짜표의 공연일, 01-09는 비공연일이다.
        rows = [
            ['1', '북2문입구#1', '2025-01-10 18:00:00', '동2문출구#2', '2025-01-10 21:00:00'],
            ['2', '북2문입구#2', '2025-01-09 18:00:00', '남2문출구#1', '2025-01-10 16:00:00'],
            ['3', '남2문입구#1', '2025-01-10 12:00:00', '북2문출구#1', '2025-01-10 15:59:59'],
            ['4', '북2문입구#1', '2025-01-09 18:00:00', '동2문출구#1', '2025-01-09 21:00:00'],
            ['5', '북2문입구#1', '2025-01-10 18:00:00', '알수없는출구', '2025-01-10 21:00:00'],
            ['6', '북2문입구#1', '2025-01-10 18:00:00', '동2문CMB', '2025-01-10 21:00:00'],
            ['7', '북2문입구#1', '2025-01-10 22:00:00', '동2문출구#1', '2025-01-10 21:00:00'],
            ['8', '북2문입구#1', '2025-12-31 18:00:00', '동2문출구#1', '2026-01-01 00:00:00'],
            ['9', '북2문입구#1', '시각없음', '동2문출구#1', '2025-01-10 21:00:00'],
        ]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path = folder / 'sample.csv.gz'
            with gzip.open(path, 'wt', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['id', 'entry_gate', 'entry_time', 'exit_gate', 'exit_time'])
                writer.writerows(rows)
            with contextlib.redirect_stdout(io.StringIO()):
                result = analyze(path, folder)
            self.assertEqual(result['audit'], {
                'input_rows': 9, 'invalid_time': 1, 'outside_2025_exit': 1,
                '2025_exit_rows': 7, 'entry_after_exit': 1, 'nonstandard_lane_rows': 2,
            })
            scopes = result['scopes']
            self.assertEqual(scopes['2025_all']['rows'], 4)
            self.assertEqual(scopes['2025_event_16_24']['rows'], 2)
            same_day = scopes['2025_event_16_24_same_day']
            self.assertEqual(same_day['rows'], 1)
            self.assertEqual(same_day['by_entry']['북2문']['exit_shares']['동2문'], 1)
            self.assertIsNone(same_day['by_entry']['동2문']['exit_shares']['북2문'])
            # 결과가 원본에 없는 주차 위치를 만들어내지 않아야 한다.
            self.assertEqual(len(result['lot_assessments']), 6)
            self.assertTrue(all(lot['exit_ids_source'] == '가정' for lot in result['lot_assessments']))
            with (folder / 'parking-exit-matrix.csv').open(encoding='utf-8-sig') as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 147)


if __name__ == '__main__':
    unittest.main()
