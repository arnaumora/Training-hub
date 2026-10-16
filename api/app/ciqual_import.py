"""Import the ANSES CIQUAL food composition table (XML release) into `food` (roadmap step 5).

Usage (inside the API image, with the release's XML files mounted read-only at /ciqual):
    python -m app.ciqual_import /ciqual            # import
    python -m app.ciqual_import /ciqual --dry-run  # parse and report, write nothing

The raw files are archived under ./data/raw/ciqual/<release>/ before importing, so the
table can always be rebuilt. Re-running is safe: rows are upserted by (source, source_id).

Robustness, since the files' details are not under our control:
- Files are found by prefix (alim_, alim_grp_, compo_, const_); the release date comes from the name.
- The encoding is read from the XML declaration (CIQUAL uses windows-1252, which Python's expat
  cannot decode itself), and the 70 MB composition file is streamed, never loaded whole.
- Records are recognised by their child fields, not by element names.
- The seven constituents are found by name (English or French), not by code. The importer prints what it
  matched and stops if a name matches twice or if carbohydrate or energy is missing.

Values: "-" or empty = unknown (NULL); "traces" = 0; "< x" = x (the upper bound); decimal comma accepted.
"""

import codecs
import re
import sys
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree.ElementTree import XMLPullParser

import psycopg
from psycopg.rows import dict_row

from .common import conninfo
from .own_foods import recompute_recipes
from .text import normalize

CHUNK = 1 << 20

# Constituents we keep, matched against names with accents removed and lower-cased.
# CIQUAL "Glucides" / "Carbohydrate" is available carbohydrate: fibre is excluded (EU convention).
CONSTITUENTS = {
    "kcal": [r"^energy, regulation eu no 1169/2011 .*kcal", r"^energie, reglement ue n\S* 1169/2011 .*kcal"],
    "carbs": [r"^carbohydrate \(g/100 ?g\)", r"^glucides \(g/100 ?g\)"],
    "sugars": [r"^sugars \(g/100 ?g\)", r"^sucres \(g/100 ?g\)"],
    "fibre": [r"^fibres?,? dietary", r"^fibres alimentaires"],
    "protein": [r"^protein, n x jones", r"^proteines, n x facteur de jones"],
    "fat": [r"^fat \(g/100 ?g\)", r"^lipides \(g/100 ?g\)"],
    "salt": [r"^salt \(g/100 ?g\)", r"^sel chlorure de sodium"],
}
REQUIRED = ("carbs", "kcal")
LIMITS = {"kcal": Decimal("99999.9")}  # every other value is g per 100 g


class ImportProblem(Exception):
    pass


def parse_value(raw: str | None) -> Decimal | None:
    s = (raw or "").strip()
    if s in ("", "-"):
        return None
    if s.lower().startswith("trace"):
        return Decimal(0)
    if s.startswith("<"):
        s = s[1:].strip()
    try:
        return Decimal(s.replace(",", "."))
    except InvalidOperation:
        return None


def records(path: Path, key_field: str) -> Iterator[dict]:
    """Stream the records of a CIQUAL XML file: every element that has a `key_field` child,
    as {child tag: stripped text}. Elements are dropped from the tree once read."""
    with path.open("rb") as f:
        head = f.read(4096)
        m = re.match(rb"\s*<\?xml[^>]*?encoding=[\"']([A-Za-z0-9_.\-]+)[\"']", head)
        decoder = codecs.getincrementaldecoder(m.group(1).decode() if m else "utf-8")(errors="strict")
        parser = XMLPullParser(events=("start", "end"))
        # The parser receives already-decoded text, so the declaration (with its encoding) must go.
        text = re.sub(r"^\s*<\?xml[^>]*\?>", "", decoder.decode(head), count=1)
        stack: list = []
        while True:
            parser.feed(text)
            for event, elem in parser.read_events():
                if event == "start":
                    stack.append(elem)
                    continue
                stack.pop()
                if elem.find(key_field) is not None:
                    yield {c.tag: (c.text or "").strip() for c in elem}
                    if stack:
                        stack[-1].remove(elem)
            chunk = f.read(CHUNK)
            if not chunk:
                break
            text = decoder.decode(chunk)
        parser.feed(decoder.decode(b"", final=True))
        parser.close()


def find_files(folder: Path) -> tuple[dict[str, Path], str]:
    found = {}
    for kind, pattern in (("alim", r"^alim_(\d{4}_\d{2}_\d{2})\.xml$"), ("alim_grp", r"^alim_grp_(\d{4}_\d{2}_\d{2})\.xml$"),
                          ("compo", r"^compo_(\d{4}_\d{2}_\d{2})\.xml$"), ("const", r"^const_(\d{4}_\d{2}_\d{2})\.xml$")):
        matches = [p for p in folder.iterdir() if re.match(pattern, p.name)]
        if len(matches) != 1:
            raise ImportProblem(f"expected exactly one {kind}_<date>.xml in {folder}, found {len(matches)}")
        found[kind] = matches[0]
    dates = {re.search(r"(\d{4}_\d{2}_\d{2})", p.name).group(1) for p in found.values()}
    if len(dates) != 1:
        raise ImportProblem(f"the files are from different releases: {sorted(dates)}")
    return found, "ciqual-" + dates.pop().replace("_", "-")


def match_constituents(rows: list[dict]) -> dict[str, dict]:
    """{key: const row} for the constituents we keep. Raises on ambiguity or a missing required one."""
    out: dict[str, dict] = {}
    for key, patterns in CONSTITUENTS.items():
        hits = []
        for row in rows:
            names = [normalize(row.get("const_nom_eng")), normalize(row.get("const_nom_fr"))]
            if any(re.search(p, n) for p in patterns for n in names):
                hits.append(row)
        if len(hits) > 1:
            listed = "; ".join(f"{h.get('const_code')} {h.get('const_nom_eng')}" for h in hits)
            raise ImportProblem(f"'{key}' matches more than one constituent: {listed}")
        if hits:
            out[key] = hits[0]
    missing = [k for k in REQUIRED if k not in out]
    if missing:
        raise ImportProblem(f"required constituents not found: {', '.join(missing)}")
    return out


def load(folder: Path) -> tuple[str, dict[str, dict], list[dict]]:
    """Parse a release folder. Returns (version, matched constituents, food rows ready to upsert)."""
    files, version = find_files(folder)
    consts = match_constituents(list(records(files["const"], "const_code")))
    code_to_key = {row["const_code"]: key for key, row in consts.items()}

    groups = {}
    for g in records(files["alim_grp"], "alim_grp_code"):
        groups.setdefault(g["alim_grp_code"], (g.get("alim_grp_nom_eng") or None, g.get("alim_grp_nom_fr") or None))

    foods = {}
    for a in records(files["alim"], "alim_nom_fr"):
        code = a.get("alim_code", "")
        if not code:
            continue
        en, fr = a.get("alim_nom_eng") or None, a.get("alim_nom_fr") or None
        g_en, g_fr = groups.get(a.get("alim_grp_code", ""), (None, None))
        foods[code] = {
            "source_id": code, "name_en": en, "name_fr": fr, "group_en": g_en, "group_fr": g_fr,
            "search_text": normalize(f"{en or ''} {fr or ''}"),
            **{k: None for k in CONSTITUENTS},
        }

    for c in records(files["compo"], "teneur"):
        key = code_to_key.get(c.get("const_code", ""))
        food = foods.get(c.get("alim_code", ""))
        if key and food is not None:
            value = parse_value(c.get("teneur"))
            if value is not None:
                food[key] = min(value, LIMITS.get(key, Decimal(100)))
    return version, consts, [f for f in foods.values() if f["name_en"] or f["name_fr"]]


COLUMNS = ["source_id", "name_en", "name_fr", "group_en", "group_fr", "search_text", *CONSTITUENTS]


def upsert(conn, version: str, rows: list[dict]) -> int:
    cols = ", ".join(["source", "source_version", *COLUMNS])
    vals = ", ".join(["'ciqual'", "%(source_version)s", *[f"%({c})s" for c in COLUMNS]])
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in ["source_version", *COLUMNS[1:]]) + ", imported_at = now()"
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO food ({cols}) VALUES ({vals}) ON CONFLICT (source, source_id) DO UPDATE SET {updates}",
            [{**r, "source_version": version} for r in rows],
        )
    return len(rows)


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    folder, dry = Path(argv[0]), "--dry-run" in argv
    try:
        version, consts, rows = load(folder)
    except ImportProblem as e:
        print(f"IMPORT STOPPED: {e}")
        return 1
    print(f"release {version}: {len(rows)} foods")
    for key in CONSTITUENTS:
        row = consts.get(key)
        known = sum(1 for r in rows if r[key] is not None)
        label = f"{row['const_code']} {row.get('const_nom_eng') or row.get('const_nom_fr')}" if row else "NOT FOUND"
        print(f"  {key:8s} <- {label}   ({known} foods with a value)")
    if dry:
        print("dry run: nothing written")
        return 0
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        n = upsert(conn, version, rows)
        total = conn.execute("SELECT count(*) AS n FROM food WHERE source = 'ciqual'").fetchone()["n"]
        # Recipes are computed from their ingredients' values: recompute them all, in the same transaction.
        recipes = recompute_recipes(conn)
    print(f"upserted {n} foods; food table now holds {total} CIQUAL foods; recomputed {recipes} recipes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
