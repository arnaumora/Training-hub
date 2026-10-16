"""Makes the synthetic FIT files the parser tests use. Not run by the tests; the .fit files are committed.

Needs fit-tool (pip install fit-tool), which only exists to write these files:
    python tests/data/make_fit_fixtures.py

run.fit       a 30 min run: 1 s records, two 15 min laps, heart rate, speed, altitude, cadence, GPS
brick.fit     one file, two sessions (multisport): 10 min of cycling, then 5 min of running
truncated.fit run.fit cut in half (a broken download)
"""

import math
from datetime import UTC, datetime
from pathlib import Path

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import Event, EventType, FileType, Manufacturer, Sport, SubSport

HERE = Path(__file__).parent
START = datetime(2026, 10, 10, 7, 0, 0, tzinfo=UTC)  # 09:00 in Madrid


def ms(dt_s: float) -> int:
    return round((START.timestamp() + dt_s) * 1000)


def records(t0: int, seconds: int, speed: float, hr0: int, hr1: int, dist0: float = 0.0):
    out, dist = [], dist0
    for i in range(seconds):
        dist += speed if i else 0.0
        m = RecordMessage()
        m.timestamp = ms(t0 + i)
        m.distance = dist
        m.speed = speed
        m.heart_rate = round(hr0 + (hr1 - hr0) * i / max(1, seconds - 1))
        m.altitude = 200 + 10 * math.sin(i / 120)
        m.cadence = 85
        m.position_lat = 48.85 + dist / 111_000
        m.position_long = 2.35
        out.append(m)
    return out, dist


def summary(cls, t0: int, seconds: int, dist: float, recs, sport, sub_sport, **extra):
    m = cls()
    m.timestamp = ms(t0 + seconds)
    m.start_time = ms(t0)
    m.total_elapsed_time = seconds
    m.total_timer_time = seconds
    m.total_distance = dist
    hrs = [r.heart_rate for r in recs]
    m.avg_heart_rate = round(sum(hrs) / len(hrs))
    m.max_heart_rate = max(hrs)
    m.sport = sport
    m.sub_sport = sub_sport
    for k, v in extra.items():
        setattr(m, k, v)
    return m


def file_id(created_s: int, serial: int) -> FileIdMessage:
    m = FileIdMessage()
    m.type = FileType.ACTIVITY
    m.manufacturer = Manufacturer.GARMIN.value
    m.product = 3113
    m.serial_number = serial
    m.time_created = ms(created_s)
    return m


def event(t: int, kind) -> EventMessage:
    m = EventMessage()
    m.event = Event.TIMER
    m.event_type = kind
    m.timestamp = ms(t)
    return m


def run() -> bytes:
    b = FitFileBuilder(auto_define=True, min_string_size=50)
    b.add(file_id(0, 3_999_000_001))
    b.add(event(0, EventType.START))
    lap1, d1 = records(0, 900, 3.0, 130, 150)
    lap2, d2 = records(900, 900, 3.0, 150, 165, dist0=d1 + 3.0)
    b.add_all(lap1)
    b.add(summary(LapMessage, 0, 900, d1, lap1, Sport.RUNNING, SubSport.GENERIC))
    b.add_all(lap2)
    b.add(summary(LapMessage, 900, 900, d2 - d1, lap2, Sport.RUNNING, SubSport.GENERIC))
    b.add(event(1800, EventType.STOP_ALL))
    b.add(summary(SessionMessage, 0, 1800, d2, lap1 + lap2, Sport.RUNNING, SubSport.GENERIC,
                  total_ascent=42, total_descent=40, total_calories=380, first_lap_index=0, num_laps=2))
    a = ActivityMessage()
    a.timestamp = ms(1800)
    a.num_sessions = 1
    a.total_timer_time = 1800
    b.add(a)
    return b.build().to_bytes()


def brick() -> bytes:
    b = FitFileBuilder(auto_define=True, min_string_size=50)
    b.add(file_id(3600, 3_999_000_001))
    b.add(event(3600, EventType.START))
    bike, bd = records(3600, 600, 8.0, 120, 140)
    b.add_all(bike)
    b.add(summary(LapMessage, 3600, 600, bd, bike, Sport.CYCLING, SubSport.ROAD))
    b.add(summary(SessionMessage, 3600, 600, bd, bike, Sport.CYCLING, SubSport.ROAD, first_lap_index=0, num_laps=1))
    runr, rd = records(4200, 300, 3.3, 145, 160)
    b.add_all(runr)
    b.add(summary(LapMessage, 4200, 300, rd, runr, Sport.RUNNING, SubSport.GENERIC))
    b.add(summary(SessionMessage, 4200, 300, rd, runr, Sport.RUNNING, SubSport.GENERIC, first_lap_index=1, num_laps=1))
    b.add(event(4500, EventType.STOP_ALL))
    a = ActivityMessage()
    a.timestamp = ms(4500)
    a.num_sessions = 2
    a.total_timer_time = 900
    b.add(a)
    return b.build().to_bytes()


if __name__ == "__main__":
    data = run()
    (HERE / "run.fit").write_bytes(data)
    (HERE / "brick.fit").write_bytes(brick())
    (HERE / "truncated.fit").write_bytes(data[: len(data) // 2])
    print({p.name: p.stat().st_size for p in HERE.glob("*.fit")})
