"""The .ics reader (app/ics.py) and how plan sessions are read from it (app/plan.py: sport, planned distance)."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app import ics
from app.plan import _row, planned_m, sport_of

MADRID = ZoneInfo("Europe/Madrid")

CAL = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "X-WR-CALNAME:Half M - Running\r\n"
    "X-WR-TIMEZONE:Europe/Madrid\r\n"
    "BEGIN:VTIMEZONE\r\nTZID:Europe/Madrid\r\nBEGIN:STANDARD\r\nDTSTART:19701025T030000\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:a-1@x\r\n"
    "DTSTART;TZID=Europe/Madrid:20261124T080000\r\n"
    "DTEND;TZID=Europe/Madrid:20261124T091500\r\n"
    "SUMMARY:Threshold 4x4min\r\n"
    "DESCRIPTION:THE SESSION\\n  2.5 km easy\\, 4 x 4 min at 5:40-5:50/km with 90s jog\\, 2.5 km\\n  easy. Ab\r\n"
    " out 9 km.\\n\\nWHY\\n  a\\;b \\\\ c\r\n"
    "CATEGORIES:RUN\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:a-2@x\r\n"
    "DTSTART;VALUE=DATE:20261120\r\n"
    "DTEND;VALUE=DATE:20261121\r\n"
    "SUMMARY:Rest (mandatory)\r\n"
    "CATEGORIES:REST\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:a-3@x\r\n"
    "DTSTART:20261121T080000Z\r\n"
    "DURATION:PT1H30M\r\n"
    "SUMMARY:5K TIME TRIAL\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:a-4@x\r\n"
    "DTSTART:20261122T100000\r\n"
    "SUMMARY:Gone\r\n"
    "STATUS:CANCELLED\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def test_parse_a_calendar():
    cal = ics.parse(CAL.encode())
    assert cal.name == "Half M - Running" and [e.uid for e in cal.events] == ["a-1@x", "a-2@x", "a-3@x"]  # cancelled left out
    a, rest, tt = cal.events
    assert a.start == datetime(2026, 11, 24, 8, 0, tzinfo=MADRID) and a.end == datetime(2026, 11, 24, 9, 15, tzinfo=MADRID)
    # Unfolded across the line break, and unescaped.
    assert "2.5 km easy, 4 x 4 min" in a.description and "About 9 km." in a.description and a.description.endswith("a;b \\ c")
    assert a.categories == ["RUN"] and not a.all_day
    assert rest.all_day and rest.start == date(2026, 11, 20)
    assert tt.start == datetime(2026, 11, 21, 8, 0, tzinfo=timezone.utc) and (tt.end - tt.start).seconds == 5400


def test_lf_only_and_a_bom_are_fine():
    cal = ics.parse(("﻿" + CAL.replace("\r\n", "\n")).encode())
    assert len(cal.events) == 3


@pytest.mark.parametrize(
    "text, message",
    [
        ("hello", "not an .ics calendar"),
        ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nDTSTART:20261001T080000\n", "ends in the middle"),
        ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nDTSTART:20261001T080000\nRRULE:FREQ=WEEKLY\nEND:VEVENT\nEND:VCALENDAR\n", "repeating"),
        ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:20261001T080000\nEND:VEVENT\nEND:VCALENDAR\n", "no UID"),
        ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nDTSTART:20261001T080000\nEND:VEVENT\n"
         "BEGIN:VEVENT\nUID:x\nDTSTART:20261002T080000\nEND:VEVENT\nEND:VCALENDAR\n", "share the UID"),
        ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nDTSTART:2026-10-01\nEND:VEVENT\nEND:VCALENDAR\n", "could not be read"),
    ],
)
def test_refused(text, message):
    with pytest.raises(ics.IcsProblem, match=message):
        ics.parse(text.encode())


def test_not_utf8():
    with pytest.raises(ics.IcsProblem, match="UTF-8"):
        ics.parse(b"BEGIN:VCALENDAR\n\xff\xfe")


@pytest.mark.parametrize(
    "categories, title, sport",
    [
        (["RUN"], "Easy 4 km", "running"),
        (["RACE"], "HALF MARATHON 21.1 km", "running"),
        (["SWIM"], "Swim 6x100", "swimming"),
        (["GYM"], "Gym A, light legs", "strength"),
        (["REST"], "Rest", "rest"),
        ([], "Rest (mandatory)", "rest"),
        ([], "Swim 1200 easy", "swimming"),
        ([], "Gym B", "strength"),
        ([], "Long run 12 km", "running"),
        ([], "5K TIME TRIAL", "running"),
        ([], "Yoga", "other"),
    ],
)
def test_sport(categories, title, sport):
    assert sport_of(categories, title) == sport


@pytest.mark.parametrize(
    "sport, title, description, metres",
    [
        ("running", "Easy 6 km + strides", "THE SESSION\n  6 km easy.", 6000),
        ("running", "Threshold 4x4min", "2.5 km easy, 4 x 4 min ...\n  easy. About 9 km.", 9000),
        ("running", "Threshold 5x5min", "2.5 km easy ... About\n  10 km.", 10000),  # "About" and the number on two lines
        ("running", "Hill sprints 8x12s", "2 km warm-up ... About 8 km total.", 8000),
        ("running", "5K TIME TRIAL", "2 km warm-up, 5 km as hard as you can hold", 5000),
        ("running", "HALF MARATHON 21.1 km", None, 21100),
        ("running", "Tempo 20 min", "No distance here", None),
        ("swimming", "Swim 6x100", "MAIN SET\n 6x100\n\n  Total: about 1150 m\n", 1150),
        ("swimming", "Swim 1200 easy", "no total", None),
        ("strength", "Gym A", "Squat 3x8", None),
        ("rest", "Rest", "Full rest. 5 km walk if you want", None),
    ],
)
def test_planned_distance(sport, title, description, metres):
    assert planned_m(sport, title, description) == metres


def test_row_days_are_local():
    # 23:30 UTC on 21 Nov is 00:30 on 22 Nov in Madrid: the session belongs to the 22nd.
    cal = ics.parse(b"BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:late\nDTSTART:20261121T233000Z\nSUMMARY:Easy 5 km\nEND:VEVENT\nEND:VCALENDAR\n")
    r = _row(cal.events[0])
    assert r["day"] == date(2026, 11, 22) and r["sport"] == "running" and r["planned_m"] == 5000
    rest = _row(ics.parse(CAL.encode()).events[1])
    assert rest["day"] == date(2026, 11, 20) and rest["start_ts"] is None and rest["sport"] == "rest"
