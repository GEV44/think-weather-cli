"""Time correctness: upcoming hours from *now*, local calendar days, DST."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from conftest import forecast_payload, yerevan_location
from weather_cli.providers.open_meteo import parse_forecast
from weather_cli.rendering import local_today, today_forecast, upcoming_days, upcoming_hours

NY = ZoneInfo("America/New_York")
YEREVAN_TZ = ZoneInfo("Asia/Yerevan")


def report_for(tz: str, start: datetime, days: int = 6):  # type: ignore[no-untyped-def]
    payload = forecast_payload(tz, start=start, days=days)
    return parse_forecast(payload, yerevan_location(), datetime(2026, 1, 1, tzinfo=UTC))


def test_hours_start_at_current_local_hour_not_midnight() -> None:
    report = report_for("Asia/Yerevan", datetime(2026, 10, 30, tzinfo=YEREVAN_TZ))
    now = datetime(2026, 10, 30, 14, 37, tzinfo=YEREVAN_TZ)
    hours = upcoming_hours(report.hourly, now, 12)
    assert len(hours) == 12
    assert hours[0].time == datetime(2026, 10, 30, 14, 0, tzinfo=YEREVAN_TZ)
    assert hours[-1].time == datetime(2026, 10, 31, 1, 0, tzinfo=YEREVAN_TZ)


def test_now_expressed_in_utc_selects_the_same_hours() -> None:
    report = report_for("Asia/Yerevan", datetime(2026, 10, 30, tzinfo=YEREVAN_TZ))
    local_now = datetime(2026, 10, 30, 23, 30, tzinfo=YEREVAN_TZ)
    assert upcoming_hours(report.hourly, local_now, 3) == upcoming_hours(
        report.hourly, local_now.astimezone(UTC), 3
    )


def test_late_evening_window_crosses_local_midnight() -> None:
    report = report_for("Asia/Yerevan", datetime(2026, 10, 30, tzinfo=YEREVAN_TZ))
    now = datetime(2026, 10, 30, 22, 5, tzinfo=YEREVAN_TZ)
    hours = upcoming_hours(report.hourly, now, 24)
    assert len(hours) == 24
    assert [h.time.hour for h in hours[:4]] == [22, 23, 0, 1]
    assert hours[2].time.date().isoformat() == "2026-10-31"


def test_window_is_truncated_at_end_of_data() -> None:
    report = report_for("Asia/Yerevan", datetime(2026, 10, 30, tzinfo=YEREVAN_TZ), days=1)
    now = datetime(2026, 10, 30, 20, 0, tzinfo=YEREVAN_TZ)
    assert len(upcoming_hours(report.hourly, now, 24)) == 4


def test_zero_hours_requested() -> None:
    report = report_for("Asia/Yerevan", datetime(2026, 10, 30, tzinfo=YEREVAN_TZ))
    assert upcoming_hours(report.hourly, datetime.now(UTC), 0) == []


def test_dst_fall_back_new_york_repeats_1am_with_distinct_offsets() -> None:
    # 2026-11-01: clocks go back from 02:00 EDT to 01:00 EST in New York.
    report = report_for("America/New_York", datetime(2026, 11, 1, tzinfo=NY), days=2)
    now = datetime(2026, 11, 1, 0, 30, tzinfo=NY)
    hours = upcoming_hours(report.hourly, now, 4)
    labels = [(h.time.strftime("%H:%M"), h.time.utcoffset()) for h in hours]
    assert labels == [
        ("00:00", timedelta(hours=-4)),
        ("01:00", timedelta(hours=-4)),
        ("01:00", timedelta(hours=-5)),
        ("02:00", timedelta(hours=-5)),
    ]
    first_day_hours = [h for h in report.hourly if h.time.date().isoformat() == "2026-11-01"]
    assert len(first_day_hours) == 25


def test_dst_spring_forward_new_york_skips_2am() -> None:
    report = report_for("America/New_York", datetime(2026, 3, 8, tzinfo=NY), days=2)
    now = datetime(2026, 3, 8, 1, 10, tzinfo=NY)
    hours = upcoming_hours(report.hourly, now, 3)
    assert [h.time.strftime("%H:%M") for h in hours] == ["01:00", "03:00", "04:00"]
    first_day = [h for h in report.hourly if h.time.date().isoformat() == "2026-03-08"]
    assert len(first_day) == 23


def test_daily_rows_are_local_calendar_days_from_today() -> None:
    report = report_for("Asia/Yerevan", datetime(2026, 10, 30, tzinfo=YEREVAN_TZ))
    now = datetime(2026, 10, 30, 23, 59, tzinfo=YEREVAN_TZ)
    days = upcoming_days(report, now)
    assert len(days) == 5
    assert days[0].date.isoformat() == "2026-10-30"
    assert today_forecast(report, now) is days[0]


def test_utc_now_maps_to_location_local_date() -> None:
    # 21:30 UTC on Oct 30 is already Oct 31 in Yerevan (UTC+4).
    report = report_for("Asia/Yerevan", datetime(2026, 10, 30, tzinfo=YEREVAN_TZ))
    now = datetime(2026, 10, 30, 21, 30, tzinfo=UTC)
    assert local_today(report, now).isoformat() == "2026-10-31"
    assert upcoming_days(report, now)[0].date.isoformat() == "2026-10-31"


def test_daily_values_are_used_as_provided_not_recomputed() -> None:
    report = report_for("Asia/Yerevan", datetime(2026, 10, 30, tzinfo=YEREVAN_TZ))
    assert report.daily[0].temperature_max_c == 20.0
    assert report.daily[0].temperature_min_c == 5.0
    assert report.daily[0].sunrise == datetime(2026, 10, 30, 7, 0, tzinfo=YEREVAN_TZ)
