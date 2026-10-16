"""Raw archive of every LibreLinkUp response, so readings can be re-derived later.

One gzip file per UTC day: <root>/llu/YYYY/MM/YYYY-MM-DD.jsonl.gz, one JSON line per poll.
Each append writes a new gzip member, which gzip readers treat as one continuous stream.
The auth ticket is removed before writing: the archive must never hold credentials or tokens.
"""

import copy
import gzip
import json
from datetime import UTC, datetime
from pathlib import Path


def archive_response(root: Path, raw: dict, fetched_at: datetime | None = None) -> Path:
    fetched_at = fetched_at or datetime.now(UTC)
    clean = copy.deepcopy(raw)
    clean.pop("ticket", None)
    day = fetched_at.astimezone(UTC)
    path = root / "llu" / f"{day:%Y}" / f"{day:%m}" / f"{day:%Y-%m-%d}.jsonl.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"fetched_at": fetched_at.isoformat(), "response": clean}, separators=(",", ":"))
    with gzip.open(path, "at", encoding="utf-8") as f:
        f.write(line + "\n")
    return path
