"""Cheap local checks before a candidate flag reaches the competition API."""

from __future__ import annotations

import re

_PLACEHOLDER_BODIES = {"flag", "placeholder", "example", "test", "your_flag_here"}


def validate_flag_candidate(flag: str) -> tuple[bool, str, str]:
    """Return ``(valid, reason, normalized)`` without assuming a competition format."""
    normalized = flag.strip()
    if not normalized:
        return False, "empty flag", normalized
    if len(normalized) > 4096:
        return False, "flag is unreasonably long", normalized
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        return False, "flag contains control characters", normalized

    match = re.fullmatch(r"[^{}\s]+\{([^{}]*)\}", normalized)
    if match and match.group(1).strip().casefold() in _PLACEHOLDER_BODIES:
        return False, "placeholder flag", normalized
    return True, "ok", normalized
