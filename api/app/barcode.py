"""Barcode lookup: your foods first, then Open Food Facts once (roadmap step 5).

GET /api/barcode/{code}:
  1. The code must be a GTIN with a correct check digit (EAN-13, EAN-8, UPC-A, GTIN-14); a wrong check digit
     is almost always a misread, so it is refused rather than looked up.
  2. A food that is not deleted and holds this barcode (an own food created after a scan, or an Open Food
     Facts product fetched before) is returned at once. Nothing leaves the server.
  3. Otherwise Open Food Facts is asked once. Its answer is archived raw before it is parsed. A product with
     a carb value per 100 g that passes the same typo guards as own foods is stored as a food (source 'off')
     and returned. Anything else comes back as "not_found" or "incomplete" with whatever values were usable,
     so the app can open "New food" with them filled in and the barcode attached.

Open Food Facts rules (https://openfoodfacts.github.io/openfoodfacts-server/api/, read 2026-10-13): reads need
no account but a custom User-Agent "AppName/Version (ContactEmail)"; product reads are limited to 15 per minute
per IP. This server stays under 12 per minute. The contact comes from OFF_CONTACT in .env, never from code.

Carbs per 100 g on OFF are "carbohydrates_100g", which on EU labels is available carbohydrate (fibre
excluded), the same convention as CIQUAL.
"""

import gzip
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row

from .common import conninfo, require_auth
from .foods import NUTRIENTS, SELECT, food_row
from .own_foods import MAX_KCAL, check_values
from .text import normalize

log = logging.getLogger("traininghub.barcode")
router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

OFF_URL = "https://world.openfoodfacts.org/api/v2/product/{code}"
OFF_FIELDS = (
    "code,product_name,product_name_es,product_name_en,generic_name,generic_name_es,brands,"
    "nutriments,nutrition_data_per,no_nutrition_data,last_modified_t"
)
OFF_TIMEOUT_S = 8
OFF_MAX_PER_MINUTE = 12  # OFF allows 15 product reads per minute per IP

# Our nutrient -> the OFF nutriments key (values per 100 g; energy in kcal).
OFF_KEYS = {
    "kcal": "energy-kcal_100g",
    "carbs": "carbohydrates_100g",
    "sugars": "sugars_100g",
    "fibre": "fiber_100g",
    "protein": "proteins_100g",
    "fat": "fat_100g",
    "salt": "salt_100g",
}
KJ_PER_KCAL = Decimal("4.184")


class OffUnavailable(Exception):
    pass


# --- barcode -----------------------------------------------------------------------------------


def gtin_check_ok(digits: str) -> bool:
    """GS1 check digit: weights 3, 1, 3, ... from the digit left of the check digit, leftwards."""
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def normalize_barcode(raw: str) -> str:
    """The stored form of a scanned or typed barcode, or HTTPException 422."""
    code = "".join(raw.split())
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        raise HTTPException(status_code=422, detail="a barcode has 8, 12, 13 or 14 digits")
    if not gtin_check_ok(code):
        raise HTTPException(status_code=422, detail="the check digit does not match: probably a misread, scan again")
    if len(code) == 12:  # UPC-A is EAN-13 with a leading zero
        code = "0" + code
    if len(code) == 14 and code.startswith("0"):  # GTIN-14 with a leading zero is the EAN-13
        code = code[1:]
    return code


# --- Open Food Facts ---------------------------------------------------------------------------

_calls: deque[float] = deque()
_calls_lock = threading.Lock()


def _take_slot() -> None:
    now = time.monotonic()
    with _calls_lock:
        while _calls and now - _calls[0] > 60:
            _calls.popleft()
        if len(_calls) >= OFF_MAX_PER_MINUTE:
            raise HTTPException(status_code=429, detail="Open Food Facts allows only a few lookups a minute: wait a moment")
        _calls.append(now)


def fetch(code: str) -> tuple[int, dict | None]:
    """(HTTP status, JSON body) from Open Food Facts. Raises OffUnavailable when it does not answer usefully.
    Tests replace this function."""
    contact = os.environ.get("OFF_CONTACT", "").strip()
    agent = f"TrainingHub/1.0 ({contact})" if contact else "TrainingHub/1.0 (self-hosted personal app)"
    req = urllib.request.Request(f"{OFF_URL.format(code=code)}?fields={OFF_FIELDS}", headers={"User-Agent": agent})
    try:
        with urllib.request.urlopen(req, timeout=OFF_TIMEOUT_S) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:  # OFF answers an unknown product with 404 and a small JSON body
            try:
                return 404, json.loads(e.read())
            except (ValueError, OSError):
                return 404, None
        raise OffUnavailable(f"HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        raise OffUnavailable(str(e)) from e


def archive(root: Path, code: str, status: int, body: dict | None, fetched_at: datetime) -> Path:
    """Append one OFF answer to <root>/off/YYYY/MM/YYYY-MM-DD.jsonl.gz (same layout as the LibreLinkUp archive)."""
    day = fetched_at.astimezone(UTC)
    path = root / "off" / f"{day:%Y}" / f"{day:%m}" / f"{day:%Y-%m-%d}.jsonl.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {"fetched_at": fetched_at.isoformat(), "barcode": code, "http_status": status, "response": body}, separators=(",", ":")
    )
    with gzip.open(path, "at", encoding="utf-8") as f:
        f.write(line + "\n")
    return path


def _number(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def product_name(p: dict) -> str | None:
    name = next((p.get(k).strip() for k in ("product_name_es", "product_name", "product_name_en", "generic_name_es", "generic_name")
                 if isinstance(p.get(k), str) and p.get(k).strip()), None)
    brand = (p.get("brands") or "").split(",")[0].strip() if isinstance(p.get("brands"), str) else ""
    if name and brand and normalize(brand) not in normalize(name):
        return f"{name}, {brand}"
    return name or (brand or None)


def parse_product(p: dict) -> tuple[str | None, dict, list[str]]:
    """(name, values per 100 g, problems). A value outside 0..100 g (0..900 kcal) is left out and reported."""
    n = p.get("nutriments") if isinstance(p.get("nutriments"), dict) else {}
    values: dict = {}
    problems: list[str] = []
    for key, off_key in OFF_KEYS.items():
        v = _number(n.get(off_key))
        if key == "kcal" and v is None:
            kj = _number(n.get("energy-kj_100g")) or _number(n.get("energy_100g"))  # energy_100g is in kJ
            v = (kj / KJ_PER_KCAL) if kj is not None else None
        if v is None:
            values[key] = None
            continue
        top = MAX_KCAL if key == "kcal" else Decimal(100)
        if v < 0 or v > top:
            problems.append(f"{key} {v} per 100 g is not possible")
            values[key] = None
            continue
        values[key] = v.quantize(Decimal("0.1") if key == "kcal" else Decimal("0.01"))
    if p.get("no_nutrition_data") in ("on", True, "1"):
        problems.append("Open Food Facts marks it as having no nutrition data")
    return product_name(p), values, problems


# --- endpoint ----------------------------------------------------------------------------------


def _connect():
    return psycopg.connect(conninfo(), row_factory=dict_row)


def _local(conn, code: str) -> dict | None:
    r = conn.execute(f"{SELECT} WHERE f.barcode = %s AND f.archived_at IS NULL", (code,)).fetchone()
    return food_row(r) if r else None


def _floats(values: dict) -> dict:
    return {k: (None if values.get(k) is None else float(values[k])) for k in NUTRIENTS}


@router.get("/barcode/{raw_code}")
def lookup(raw_code: str):
    code = normalize_barcode(raw_code)
    with _connect() as conn:
        found = _local(conn, code)
    if found:
        return {"status": "found", "fetched": False, "food": found}

    _take_slot()
    fetched_at = datetime.now(UTC)
    try:
        status, body = fetch(code)
    except OffUnavailable as e:
        log.warning("Open Food Facts lookup failed for %s: %s", code, e)
        raise HTTPException(status_code=502, detail="Open Food Facts did not answer: try again, or enter it as your own food") from e
    try:
        archive(Path(os.environ.get("RAW_DIR", "/raw")), code, status, body, fetched_at)
    except OSError as e:
        # Raw payloads are archived before parsing (CLAUDE.md); without the archive nothing is stored.
        log.error("could not archive the Open Food Facts answer: %s", e)
        raise HTTPException(status_code=503, detail="the raw archive is not writable: nothing was stored") from e

    product = body.get("product") if isinstance(body, dict) and body.get("status") == 1 else None
    if status == 404 or not isinstance(product, dict):
        return {"status": "not_found", "barcode": code}

    name, values, problems = parse_product(product)
    prefill = {"name": name or "", "per100": _floats(values)}
    if values["carbs"] is None:
        return {"status": "incomplete", "barcode": code, "reason": "Open Food Facts has no carb value per 100 g for it", "prefill": prefill}
    try:
        check_values(values, name or code)
    except HTTPException as e:
        problems.append(str(e.detail))
    if problems:
        return {"status": "incomplete", "barcode": code, "reason": "; ".join(problems), "prefill": prefill}

    name = name or f"Product {code}"
    with _connect() as conn:
        food_id = conn.execute(
            f"""INSERT INTO food (source, source_id, source_version, barcode, name_en, search_text, {", ".join(NUTRIENTS)})
                VALUES ('off', %(code)s, %(version)s, %(code)s, %(name)s, %(search)s, {", ".join(f"%({n})s" for n in NUTRIENTS)})
                ON CONFLICT (source, source_id) DO UPDATE SET
                    source_version = EXCLUDED.source_version, barcode = EXCLUDED.barcode, name_en = EXCLUDED.name_en,
                    search_text = EXCLUDED.search_text, {", ".join(f"{n} = EXCLUDED.{n}" for n in NUTRIENTS)},
                    imported_at = now()
                RETURNING id""",
            {**values, "code": code, "version": f"off-{fetched_at:%Y-%m-%d}", "name": name, "search": normalize(f"{name} {code}")},
        ).fetchone()["id"]
        food = food_row(conn.execute(f"{SELECT} WHERE f.id = %s", (food_id,)).fetchone())
    return {"status": "found", "fetched": True, "food": food}
