"""Parsing and formatting of user-entered time values."""

import re

_NUM = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)")


def parse_time(text: str) -> float:
    """Parse ``HH:MM:SS``, ``MM:SS`` or raw seconds into seconds.

    Fractions of a second (``.ms``, ``,ms``) are allowed on the last component.
    """
    raw = (text or "").strip().replace(",", ".")
    if not raw:
        raise ValueError("Time is empty.")
    parts = [p.strip() for p in raw.split(":")]
    if len(parts) > 3 or not all(_NUM.fullmatch(p) for p in parts):
        raise ValueError(
            f"'{raw}' is not a valid time. Use HH:MM:SS, MM:SS or seconds."
        )
    nums = [float(p) for p in parts]
    if len(nums) > 1:
        if any(n != int(n) for n in nums[:-1]):
            raise ValueError(f"'{raw}': hours and minutes must be whole numbers.")
        if nums[-1] >= 60 or (len(nums) == 3 and nums[1] >= 60):
            raise ValueError(f"'{raw}': minutes and seconds must be below 60.")
    total = 0.0
    for n in nums:
        total = total * 60 + n
    return total


def format_time(seconds: float) -> str:
    """Format seconds as ``HH:MM:SS.mmm``."""
    ms = round(max(seconds, 0.0) * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
