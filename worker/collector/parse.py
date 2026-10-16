"""Parse LibreLinkUp graph responses into readings.

Deliberately minimal: only the three fields we store are read, so changes Abbott makes to
unrelated parts of the response cannot break collection.

Response shape (relevant parts only):
  data.connection.glucoseMeasurement  -> the current reading, with TrendArrow
  data.graphData[]                    -> about 12 h of history, one point per 15 min
Each measurement has FactoryTimestamp ("10/12/2026 2:45:59 PM", UTC), ValueInMgPerDl, and
(current only) TrendArrow 1..5.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

TS_FORMAT = "%m/%d/%Y %I:%M:%S %p"


@dataclass(frozen=True)
class Reading:
    ts: datetime  # exact sensor time, UTC
    mg_dl: int
    trend: int | None  # 0..5, only on the current reading

    @property
    def minute(self) -> datetime:
        return self.ts.replace(second=0, microsecond=0)


def parse_measurement(m: dict | None, with_trend: bool) -> Reading | None:
    """Return a Reading, or None for a missing or unusable measurement."""
    if not m:
        return None
    try:
        ts = datetime.strptime(m["FactoryTimestamp"], TS_FORMAT).replace(tzinfo=UTC)
        value = float(m["ValueInMgPerDl"])
    except (KeyError, TypeError, ValueError):
        return None
    mg_dl = round(value)
    if not 0 < mg_dl < 1000:
        return None
    trend = None
    if with_trend:
        raw_trend = m.get("TrendArrow")
        if isinstance(raw_trend, int) and 0 <= raw_trend <= 5:
            trend = raw_trend
    return Reading(ts=ts, mg_dl=mg_dl, trend=trend)


def parse_graph(raw: dict) -> tuple[Reading | None, list[Reading]]:
    """Split a graph response into (current reading, history readings)."""
    data = raw.get("data") or {}
    connection = data.get("connection") or {}
    current = parse_measurement(connection.get("glucoseMeasurement"), with_trend=True)
    history = [r for r in (parse_measurement(m, with_trend=False) for m in data.get("graphData") or []) if r]
    return current, history
