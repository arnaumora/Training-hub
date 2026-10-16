"""A small iCalendar (.ics, RFC 5545) reader for the training plan (roadmap step 9, change 1).

Only what a plan calendar needs: the calendar's name and its events (VEVENT) with UID, start, end, summary,
description, categories and status. Written here rather than taken from a library because the plan files
are simple and fully under our control, and every rule below is then visible and tested.

- Lines are unfolded (a line starting with a space or tab continues the one before), CRLF or LF.
- TEXT values are unescaped: \\n and \\N to a newline, \\, \\; and \\\\ to the character.
- DTSTART/DTEND: a date (VALUE=DATE: all day), a UTC time ("...Z"), a time with TZID, or a floating time,
  which is read in the calendar's X-WR-TIMEZONE, else Europe/Madrid. An unknown TZID is read the same way.
- Not supported, and refused with a message rather than read wrongly: repeating events (RRULE, RDATE,
  RECURRENCE-ID) and two events with the same UID.
- STATUS:CANCELLED events are left out, as if they were not in the file.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "Europe/Madrid"
MAX_EVENTS = 2000


class IcsProblem(Exception):
    """The file cannot be read as a plan; the message says why, in words for the user."""


@dataclass
class Event:
    uid: str
    start: date | datetime  # a date for all-day events, else an aware datetime
    end: date | datetime | None
    summary: str
    description: str | None
    categories: list[str]

    @property
    def all_day(self) -> bool:
        return not isinstance(self.start, datetime)


@dataclass
class Calendar:
    name: str | None
    tz: str
    events: list[Event]


def _unfold(text: str) -> list[str]:
    out: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        elif line:
            out.append(line)
    return out


_PARAM = re.compile(r';([A-Za-z0-9-]+)=("[^"]*"|[^;:]*)')


def _split(line: str) -> tuple[str, dict[str, str], str]:
    """'DTSTART;TZID=Europe/Madrid:20261002T080000' -> ('DTSTART', {'TZID': 'Europe/Madrid'}, '20261002T080000')."""
    m = re.match(r"([A-Za-z0-9-]+)((?:;[A-Za-z0-9-]+=(?:\"[^\"]*\"|[^;:]*))*):(.*)", line)
    if not m:
        return "", {}, ""
    params = {k.upper(): v.strip('"') for k, v in _PARAM.findall(m.group(2))}
    return m.group(1).upper(), params, m.group(3)


def unescape(value: str) -> str:
    return re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value)


def _zone(name: str | None, fallback: str) -> ZoneInfo:
    for n in (name, fallback, DEFAULT_TZ):
        if n:
            try:
                return ZoneInfo(n)
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return ZoneInfo(DEFAULT_TZ)


def _when(value: str, params: dict[str, str], cal_tz: str) -> date | datetime:
    v = value.strip()
    try:
        if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", v):
            return datetime.strptime(v, "%Y%m%d").date()
        if v.endswith("Z"):
            return datetime.strptime(v[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        return datetime.strptime(v, "%Y%m%dT%H%M%S").replace(tzinfo=_zone(params.get("TZID"), cal_tz))
    except ValueError:
        raise IcsProblem(f"a date or time could not be read: {v}") from None


def parse(data: bytes) -> Calendar:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise IcsProblem("the file is not UTF-8 text") from None
    lines = _unfold(text)
    if not lines or lines[0].strip().upper() != "BEGIN:VCALENDAR":
        raise IcsProblem("this is not an .ics calendar file")

    name, cal_tz = None, DEFAULT_TZ
    raw_events: list[list[tuple[str, dict, str]]] = []
    stack: list[str] = []
    for line in lines:
        prop, params, value = _split(line)
        if prop == "BEGIN":
            stack.append(value.upper())
            if value.upper() == "VEVENT":
                raw_events.append([])
            continue
        if prop == "END":
            if stack:
                stack.pop()
            continue
        if stack == ["VCALENDAR"]:
            if prop == "X-WR-CALNAME":
                name = unescape(value).strip() or None
            elif prop == "X-WR-TIMEZONE":
                cal_tz = value.strip() or DEFAULT_TZ
        elif stack[-1:] == ["VEVENT"] and len(stack) == 2:
            raw_events[-1].append((prop, params, value))
    if stack:
        raise IcsProblem("the file ends in the middle of a calendar (is it complete?)")
    if len(raw_events) > MAX_EVENTS:
        raise IcsProblem(f"the file has more than {MAX_EVENTS} events")

    events: list[Event] = []
    seen: set[str] = set()
    for props in raw_events:
        get = {p: (pa, v) for p, pa, v in props}  # one of each is enough for these properties
        if any(p in get for p in ("RRULE", "RDATE", "RECURRENCE-ID")):
            raise IcsProblem("repeating events are not supported: export the plan with one event per session")
        if "UID" not in get or "DTSTART" not in get:
            raise IcsProblem("an event has no UID or no start")
        if get.get("STATUS", ({}, ""))[1].strip().upper() == "CANCELLED":
            continue
        uid = get["UID"][1].strip()
        if uid in seen:
            raise IcsProblem(f"two events share the UID {uid}")
        seen.add(uid)
        start = _when(get["DTSTART"][1], get["DTSTART"][0], cal_tz)
        end = _when(get["DTEND"][1], get["DTEND"][0], cal_tz) if "DTEND" in get else None
        if end is None and "DURATION" in get and isinstance(start, datetime):
            end = start + _duration(get["DURATION"][1])
        categories = [unescape(c).strip().upper() for p, _, v in props if p == "CATEGORIES" for c in re.split(r"(?<!\\),", v)]
        events.append(Event(
            uid=uid,
            start=start,
            end=end,
            summary=unescape(get.get("SUMMARY", ({}, ""))[1]).strip(),
            description=(unescape(get["DESCRIPTION"][1]).strip() or None) if "DESCRIPTION" in get else None,
            categories=[c for c in categories if c],
        ))
    return Calendar(name=name, tz=cal_tz, events=events)


def _duration(value: str) -> timedelta:
    m = re.fullmatch(r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip())
    if not m:
        raise IcsProblem(f"a duration could not be read: {value}")
    w, d, h, mi, s = (int(x or 0) for x in m.groups())
    return timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)
