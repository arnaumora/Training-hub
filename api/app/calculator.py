"""Bolus calculator (roadmap step 4). Pure and deterministic: no database, no clock, no I/O.

Same inputs, same result, so every number can be checked by hand and by the unit tests.
All insulin parameters come from one saved Settings > Insulin version; nothing here has a default
for them. The only constants below are safety thresholds for when to warn or not to suggest.

Formula (spec, "Insulin and carbs log, and calculator safety rules"), with the IOB rule agreed on
2026-10-12:

    carbs part      = raciones * ratio of the meal slot at the current local time
    correction      = (glucose - correct_to) / isf, only when glucose > correct_above, else 0
    insulin active  = sum over rapid doses of units * (1 - elapsed / action_time), for elapsed < action_time
    net correction  = max(0, correction - insulin active)      # IOB never reduces the carbs part
    total           = carbs part + net correction
    suggested       = total rounded DOWN to the pen increment

Why IOB only offsets the correction: insulin from an earlier meal is mostly covering that meal's carbs,
which are still being absorbed, so taking it off a new meal would count it twice. Taking it off a
correction prevents the real risk, stacking corrections on a high that earlier insulin is already
bringing down.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")

# Safety thresholds (not insulin parameters).
STALE_AFTER = timedelta(minutes=10)  # spec rule 5: warn when the reading is older than this
TOO_OLD = timedelta(minutes=30)  # older than this: no correction is computed at all
LOW_MG_DL = 70  # below this: no dose is suggested, the low comes first
YOUNG_LOG = timedelta(days=14)  # until 14 days after the first logged rapid dose, a correction carries a warning

TREND_WORDS = {1: "falling fast", 2: "falling", 3: "stable", 4: "rising", 5: "rising fast"}


@dataclass(frozen=True)
class Glucose:
    ts: datetime
    mg_dl: int
    trend: int | None  # 1..5, None for 15-min history points


@dataclass(frozen=True)
class Dose:
    ts: datetime
    units: Decimal


@dataclass
class Result:
    settings_version: int
    slot: str
    ratio: Decimal
    isf: Decimal
    correct_above: int
    correct_to: int
    raciones: Decimal | None
    carbs_units: Decimal
    glucose: Glucose | None
    glucose_age_min: int | None
    correction_units: Decimal | None  # None: not computed (no recent reading)
    iob_units: Decimal
    iob_used: Decimal  # the part of IOB taken off the correction
    total_units: Decimal
    suggested_units: Decimal | None  # None: no dose suggested (glucose low)
    pen_increment: Decimal
    warnings: list[str] = field(default_factory=list)  # about this moment: shown next to the suggestion
    notes: list[str] = field(default_factory=list)  # standing reminders: shown with the breakdown

    def as_dict(self) -> dict:
        """JSON-friendly, with every term as a number rounded for display (2 decimals)."""

        def n(v):
            return None if v is None else float(round(v, 2))

        g = self.glucose
        return {
            "settings_version": self.settings_version,
            "slot": self.slot,
            "ratio": n(self.ratio),
            "isf": n(self.isf),
            "correct_above": self.correct_above,
            "correct_to": self.correct_to,
            "raciones": n(self.raciones),
            "carbs_units": n(self.carbs_units),
            "glucose": None if g is None else {"mg_dl": g.mg_dl, "trend": g.trend, "age_min": self.glucose_age_min},
            "correction_units": n(self.correction_units),
            "iob_units": n(self.iob_units),
            "iob_used": n(self.iob_used),
            "total_units": n(self.total_units),
            "suggested_units": n(self.suggested_units),
            "pen_increment": n(self.pen_increment),
            "warnings": self.warnings,
            "notes": self.notes,
        }


def slot_at(slots: list[dict], when: datetime) -> dict:
    """The meal slot for a moment: the last slot starting at or before its local (Madrid) time;
    before the first start of the day, the day's last slot (it runs past midnight)."""
    hhmm = when.astimezone(MADRID).strftime("%H:%M")
    ordered = sorted(slots, key=lambda s: s["start"])
    current = ordered[-1]
    for s in ordered:
        if s["start"] <= hhmm:
            current = s
    return current


def insulin_on_board(doses: list[Dose], now: datetime, action_time_h: Decimal) -> Decimal:
    """Linear decay: a dose counts fully when injected and fades evenly to zero over the action time.
    A dose logged a few minutes ahead of `now` counts fully."""
    action_s = action_time_h * 3600
    total = Decimal(0)
    for d in doses:
        elapsed = Decimal(max(0.0, (now - d.ts).total_seconds()))
        if elapsed < action_s:
            total += d.units * (1 - elapsed / action_s)
    return total


def floor_to(value: Decimal, step: Decimal) -> Decimal:
    if value <= 0:
        return Decimal(0)
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def calculate(
    settings: dict,
    now: datetime,
    carbs_g: Decimal | None,
    glucose: Glucose | None,
    rapid_doses: list[Dose],
    first_rapid_dose: datetime | None,
) -> Result:
    """One suggestion. `settings` is a saved Settings > Insulin version (all fields present)."""
    slot = slot_at(settings["meal_slots"], now)
    ratio = Decimal(str(slot["ratio"]))
    racion_g = Decimal(settings["racion_g"])
    isf = Decimal(settings["isf"])
    inc = Decimal(settings["pen_increment"])
    warnings: list[str] = []

    raciones = None if carbs_g is None else Decimal(carbs_g) / racion_g
    carbs_units = Decimal(0) if raciones is None else raciones * ratio

    age = None if glucose is None else now - glucose.ts
    age_min = None if age is None else max(0, int(age.total_seconds() // 60))
    correction = None
    low = False
    if glucose is None or age > TOO_OLD:
        warnings.append("No glucose reading in the last 30 min: the correction is left out. Open LibreLink or scan.")
    else:
        if age > STALE_AFTER:
            warnings.append(f"The glucose reading is {age_min} min old. Open LibreLink or scan for a fresh one.")
        if glucose.mg_dl < LOW_MG_DL:
            low = True
            warnings.append("Glucose is below 70 mg/dL: treat the low first. No dose is suggested.")
        if glucose.mg_dl > settings["correct_above"]:
            correction = Decimal(glucose.mg_dl - settings["correct_to"]) / isf
        else:
            correction = Decimal(0)
        if glucose.trend in (1, 2):
            warnings.append(f"Glucose is {TREND_WORDS[glucose.trend]}: consider less than suggested.")

    # Since 2026-10-16 the suggestion is usable from the first day (it fills the Log form); the young-log
    # risk is only in the correction (insulin on board counts logged doses only), so it is said there.
    if correction and (first_rapid_dose is None or now < first_rapid_dose + YOUNG_LOG):
        warnings.append(
            "Your rapid doses have been logged for under 14 days: active insulin only counts logged doses, "
            "so the correction may be too high."
        )

    iob = insulin_on_board(rapid_doses, now, Decimal(settings["action_time_h"]))
    iob_used = Decimal(0) if not correction else min(iob, correction)
    total = carbs_units + ((correction or Decimal(0)) - iob_used)
    suggested = None if low else floor_to(total, inc)

    notes: list[str] = []
    if settings["action_time_provisional"]:
        notes.append(f"Action time {Decimal(settings['action_time_h']).normalize()} h is provisional (not yet confirmed).")
    notes.append("Exercise is not known to the app yet: adjust for runs yourself.")

    return Result(
        settings_version=settings["version"],
        slot=slot["name"],
        ratio=ratio,
        isf=isf,
        correct_above=settings["correct_above"],
        correct_to=settings["correct_to"],
        raciones=raciones,
        carbs_units=carbs_units,
        glucose=glucose,
        glucose_age_min=age_min,
        correction_units=correction,
        iob_units=iob,
        iob_used=iob_used,
        total_units=total,
        suggested_units=suggested,
        pen_increment=inc,
        warnings=warnings,
        notes=notes,
    )
