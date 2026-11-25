from datetime import date, datetime
import unittest

from olpark_demand.schedule import parse_schedule, runtime_minutes, venue_group


def clocks(parsed):
    return [s.strftime("%m-%d %H:%M") for s in parsed.sessions]


class ScheduleNoticeTests(unittest.TestCase):
    def test_weekday_rule_after_a_leading_date_range(self):
        parsed = parse_schedule("2025년 3월 7일(금) ~ 3월 9일(일)   금 오후 8시 / 토 오후 6시 / 일 오후 5시",
                                date(2025, 3, 7), date(2025, 3, 9))
        self.assertEqual(parsed.basis, "weekday_rule")
        self.assertEqual(clocks(parsed), ["03-07 20:00", "03-08 18:00", "03-09 17:00"])

    def test_explicit_dates_and_a_bare_day_inherit_the_month(self):
        parsed = parse_schedule("12월 25일(목) 6PM, 26일(금) 8PM, 27일(토) 6PM, 28일(일) 4PM",
                                date(2025, 12, 25), date(2025, 12, 28))
        self.assertEqual(parsed.basis, "explicit_date")
        self.assertEqual(clocks(parsed), ["12-25 18:00", "12-26 20:00", "12-27 18:00", "12-28 16:00"])

    def test_dotted_dates_with_a_year_prefix(self):
        parsed = parse_schedule("2025.5.23(금) 19시 / 5.24(토) 18시 / 5.25(일) 17시25분", date(2025, 5, 23), date(2025, 5, 25))
        self.assertEqual(clocks(parsed), ["05-23 19:00", "05-24 18:00", "05-25 17:25"])

    def test_one_clock_time_applies_to_every_listed_day(self):
        # The 2025 archive set this notice aside; a reader takes 17시 for both days.
        parsed = parse_schedule("17시", date(2025, 8, 2), date(2025, 8, 3))
        self.assertEqual(parsed.basis, "single_time_all_days")
        self.assertEqual(clocks(parsed), ["08-02 17:00", "08-03 17:00"])

    def test_a_date_range_shares_the_clock_written_after_it(self):
        parsed = parse_schedule("6월 27일(토) - 28일(일) 오후1시, 오후6시", date(2026, 6, 27), date(2026, 6, 28))
        self.assertEqual(clocks(parsed), ["06-27 13:00", "06-27 18:00", "06-28 13:00", "06-28 18:00"])

    def test_afternoon_carries_through_a_list_and_unmarked_small_hours_are_afternoon(self):
        parsed = parse_schedule("2026년 1월 24일(토) 오후 1시, 6시", date(2026, 1, 24), date(2026, 1, 24))
        self.assertEqual(clocks(parsed), ["01-24 13:00", "01-24 18:00"])
        parsed = parse_schedule("화,수,목,금,일 11시, 2시 / 토 11시, 2시, 4시30분", date(2025, 3, 4), date(2025, 3, 8))
        self.assertEqual(clocks(parsed)[-3:], ["03-08 11:00", "03-08 14:00", "03-08 16:30"])

    def test_weekday_ranges_dark_days_and_holiday_rules(self):
        text = "화~금 11시, 14시 / 주말&공휴일 11시, 14시, 16시 30분 / 월 공연없음"
        parsed = parse_schedule(text, date(2025, 12, 22), date(2025, 12, 28), holidays={date(2025, 12, 25)})
        by_day = {}
        for stamp in parsed.sessions:
            by_day.setdefault(stamp.date(), []).append(stamp.strftime("%H:%M"))
        self.assertNotIn(date(2025, 12, 22), by_day)                         # Monday is dark
        self.assertEqual(by_day[date(2025, 12, 23)], ["11:00", "14:00"])
        self.assertEqual(by_day[date(2025, 12, 25)], ["11:00", "14:00", "16:30"])  # Thursday holiday
        self.assertEqual(by_day[date(2025, 12, 27)], ["11:00", "14:00", "16:30"])

    def test_a_weekday_list_narrows_the_group_before_it(self):
        parsed = parse_schedule("평일(수), (목), (금) 11시 / 주말(토), (일) 14시", date(2026, 3, 2), date(2026, 3, 8))
        self.assertEqual(clocks(parsed), ["03-04 11:00", "03-05 11:00", "03-06 11:00", "03-07 14:00", "03-08 14:00"])

    def test_explicit_exceptions_override_the_weekday_rule(self):
        text = "수,목,금 16시 30분 / 주말, 공휴일 11시, 14시, 16시 30분 *6월 3일(수) 14시, 16시 30분"
        parsed = parse_schedule(text, date(2026, 6, 2), date(2026, 6, 4))
        self.assertEqual(clocks(parsed), ["06-03 14:00", "06-03 16:30", "06-04 16:30"])

    def test_a_time_range_is_one_long_session_from_its_opening(self):
        parsed = parse_schedule("토요일 : 14:00 ~ 22:30 / 일요일 : 14:00 ~ 21:30", date(2026, 6, 20), date(2026, 6, 21))
        self.assertEqual(clocks(parsed), ["06-20 14:00", "06-21 14:00"])

    def test_a_notice_one_day_outside_the_listing_is_kept(self):
        parsed = parse_schedule("2025년 2월 21일(금) ~ 2월 23일(일)   금 오후 8시 / 토 오후 6시 / 일 오후 4시",
                                date(2025, 2, 22), date(2025, 2, 23))
        self.assertEqual(clocks(parsed), ["02-21 20:00", "02-22 18:00", "02-23 16:00"])

    def test_unreadable_notices_are_reported_not_guessed(self):
        for text in ("", "추후 공지", "※정확한 공연 시간 정보는 추후 업데이트 예정"):
            parsed = parse_schedule(text, date(2026, 5, 22), date(2026, 5, 24))
            self.assertFalse(parsed.resolved)
            self.assertEqual(parsed.basis, "unresolved")
        with self.assertRaises(ValueError):
            parse_schedule("18시", date(2026, 5, 2), date(2026, 5, 1))

    def test_returned_sessions_are_naive_local_datetimes_in_order(self):
        parsed = parse_schedule("토 오후 6시 / 일 오후 4시", date(2026, 1, 31), date(2026, 2, 1))
        self.assertEqual(parsed.sessions, (datetime(2026, 1, 31, 18), datetime(2026, 2, 1, 16)))


class RuntimeAndVenueTests(unittest.TestCase):
    def test_running_time(self):
        self.assertEqual(runtime_minutes("120분"), 120)
        self.assertEqual(runtime_minutes("총 120분"), 120)
        self.assertEqual(runtime_minutes("150분(인터미션 20분)"), 150)
        self.assertEqual(runtime_minutes("2시간 30분"), 150)
        for blank in ("", "-", "--", None, "20분", "100분 ~ 120분 110분"):
            self.assertIsNone(runtime_minutes(blank))

    def test_venue_groups(self):
        self.assertEqual(venue_group("KSPO DOME(올림픽체조경기장)"), "dome")
        self.assertEqual(venue_group("티켓링크 라이브 아레나 (구 올림픽공원 핸드볼경기장)"), "arena")
        self.assertEqual(venue_group("올림픽공원 內 올림픽홀"), "hall")
        self.assertEqual(venue_group("올림픽공원 우리금융아트홀"), "woori")
        self.assertEqual(venue_group("올림픽공원 뮤즈라이브"), "small")
        self.assertEqual(venue_group("올림픽공원 88잔디마당"), "festival")
        self.assertEqual(venue_group("KSPODOME 88잔디마당 올림픽홀", "제3회 가을 음악 페스티벌 2025"), "festival")
        self.assertEqual(venue_group("올림픽공원 "), "festival")


if __name__ == "__main__":
    unittest.main()
