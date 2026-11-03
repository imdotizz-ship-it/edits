"""Battlelog platform identifiers.

Evidence: PRIOR KNOWLEDGE (public Battlelog URLs such as
/bf4/warsawoverviewpopulate/<personaId>/<platformInt>/), not observed in the
build session because the network policy blocked battlelog.battlefield.com.
Validate with `python -m blpanel probe` before relying on them.
"""
PLATFORMS = {
    "pc": 1,
    "xbox360": 2,
    "ps3": 4,
    "ps4": 32,
    "xboxone": 64,
}
PLATFORM_LABELS = {
    "pc": "PC",
    "xbox360": "Xbox 360",
    "ps3": "PlayStation 3",
    "ps4": "PlayStation 4",
    "xboxone": "Xbox One",
}
_BY_INT = {v: k for k, v in PLATFORMS.items()}


def platform_int(name: str) -> int:
    key = name.strip().lower()
    if key not in PLATFORMS:
        raise ValueError(f"unknown platform {name!r}; expected one of {sorted(PLATFORMS)}")
    return PLATFORMS[key]


def platform_name(value: int | str) -> str | None:
    try:
        return _BY_INT.get(int(value))
    except (TypeError, ValueError):
        key = str(value).lower()
        return key if key in PLATFORMS else None
