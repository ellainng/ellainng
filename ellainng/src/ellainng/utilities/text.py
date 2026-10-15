"""Small deterministic text utilities."""

import re
import unicodedata


def slugify(value: str, separator: str = "-") -> str:
    """Turn text into an ASCII slug, using a hyphen or underscore separator."""
    if separator not in ("-", "_"):
        raise ValueError("separator must be '-' or '_'.")
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", separator, normalized.lower()).strip(separator)
