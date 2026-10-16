"""Text helpers shared by importers and search."""

import re
import unicodedata


def normalize(text: str | None) -> str:
    """Lower case, accents removed, single spaces: for matching names and for search (an accent-free query finds accented names)."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped).strip().lower()
