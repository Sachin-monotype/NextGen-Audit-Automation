"""Relaxed value equivalence for source vs enriched comparison rows."""

from __future__ import annotations

import json
import re

from .discovery_resolver import normalize_compare

_TRAILING_TEST = re.compile(r"\s+test\s*$", re.I)
_NAME_LIKE_PATHS = (
    "displayName",
    "name",
    "firstName",
    "lastName",
    "title_en",
    "name_en",
    "font_name",
)
_ARRAY_INDEX_RE = re.compile(r"\[(\d+)\]")


def _relaxed_scalar(s: str) -> str:
    t = s.strip()
    t = _TRAILING_TEST.sub("", t)
    return t.casefold()


def _is_name_like_field(field_path: str) -> bool:
    leaf = field_path.split(".")[-1].replace("[0]", "")
    return leaf in _NAME_LIKE_PATHS or any(p in field_path for p in _NAME_LIKE_PATHS)


def _parse_jsonish(val: object) -> object:
    if isinstance(val, (dict, list)):
        return val
    if not isinstance(val, str):
        return val
    s = val.strip()
    if not s or s[0] not in "[{":
        return val
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return val


def _array_index_from_path(field_path: str) -> int | None:
    trailing = field_path.rsplit(".", 1)[-1]
    m = _ARRAY_INDEX_RE.search(trailing)
    return int(m.group(1)) if m else None


def _is_language_field(field_path: str) -> bool:
    leaf = field_path.rsplit(".", 1)[-1].replace("[0]", "").lower()
    return "language" in leaf or leaf in {"locale", "locales", "lang"}


def _language_tokens(val: object) -> set[str]:
    """Normalize language codes from str / CSV / list / nested dict."""
    out: set[str] = set()

    def add(x: object) -> None:
        if x is None:
            return
        if isinstance(x, (list, tuple, set)):
            for i in x:
                add(i)
            return
        if isinstance(x, dict):
            for k in ("code", "language", "locale", "value", "id"):
                if k in x:
                    add(x[k])
            return
        s = str(x).strip()
        if not s:
            return
        if s[0] in "[{":
            parsed = _parse_jsonish(s)
            if isinstance(parsed, (list, dict)):
                add(parsed)
                return
        for part in re.split(r"[,|;/\s]+", s):
            tok = part.strip().casefold()
            if tok:
                out.add(tok)

    add(val)
    return out


def _align_source_shape(source_val: object, enriched_val: object, *, field_path: str) -> object:
    """
    Coerce Typesense/CMS shapes to match enriched scalar leaves.

    Examples:
    - enriched ``font_nids[0]`` scalar vs source ``["a", "b"]`` → compare ``"a"``
    - enriched ``visual_properties.contrast`` vs source whole object → ``contrast`` key
    - enriched ``supportedLanguage: "EN"`` vs source ``["EN","FR"]`` → ``"EN"``
    """
    source_val = _parse_jsonish(source_val)
    idx = _array_index_from_path(field_path)
    leaf = field_path.rsplit(".", 1)[-1]
    leaf_key = _ARRAY_INDEX_RE.sub("", leaf)

    if isinstance(source_val, list) and not isinstance(enriched_val, list):
        if idx is not None and 0 <= idx < len(source_val):
            return source_val[idx]
        if len(source_val) == 1:
            return source_val[0]
        # Language lists: prefer the enriched code when it is a member.
        if _is_language_field(field_path) and enriched_val is not None:
            enr = str(enriched_val).strip().casefold()
            for item in source_val:
                if str(item).strip().casefold() == enr:
                    return item

    if isinstance(source_val, dict) and not isinstance(enriched_val, dict):
        if leaf_key in source_val:
            return source_val[leaf_key]
        if "visual_properties" in field_path and leaf_key in source_val:
            return source_val[leaf_key]

    return source_val


def _yes_no_bool(val: object) -> object:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)) and val in (0, 1):
        return bool(int(val))
    if isinstance(val, str):
        low = val.strip().casefold()
        if low in {"yes", "y", "true", "1"}:
            return True
        if low in {"no", "n", "false", "0"}:
            return False
    return val


_ISO_LIKE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d+))?(?:Z|[+-]\d{2}:?\d{2})?$"
)


def _datetimes_equivalent(a: str, b: str) -> bool:
    ma, mb = _ISO_LIKE.match(a.strip()), _ISO_LIKE.match(b.strip())
    if not ma or not mb:
        return False
    if ma.group(1) != mb.group(1) or ma.group(2) != mb.group(2):
        return False
    fa = (ma.group(3) or "").ljust(6, "0")[:6]
    fb = (mb.group(3) or "").ljust(6, "0")[:6]
    # Compare at millisecond precision (AMS/CMS APIs use ms)
    return fa[:3] == fb[:3]


def values_equivalent(
    source_val: object,
    enriched_val: object,
    *,
    field_path: str = "",
) -> bool:
    """True when values match exactly or differ only by casing / minor label noise."""
    source_val = _align_source_shape(source_val, enriched_val, field_path=field_path)
    source_val = _yes_no_bool(source_val)
    enriched_val = _yes_no_bool(enriched_val)

    # Language codes: membership when CMS stores a list / CSV and enricher echoes one.
    if _is_language_field(field_path):
        src_toks = _language_tokens(source_val)
        enr_toks = _language_tokens(enriched_val)
        if src_toks and enr_toks and (enr_toks <= src_toks or src_toks <= enr_toks or src_toks & enr_toks):
            return True

    if isinstance(source_val, (dict, list)) or isinstance(enriched_val, (dict, list)):
        return normalize_compare(source_val) == normalize_compare(enriched_val)

    sv = normalize_compare(source_val)
    ev = normalize_compare(enriched_val)
    if sv == ev:
        return True
    if not sv or not ev:
        return False

    rsv, rev = _relaxed_scalar(sv), _relaxed_scalar(ev)
    if rsv == rev:
        return True

    # Timestamp parity: 2026-06-29T06:34:38.855Z == …855000Z == …855+00:00
    if _datetimes_equivalent(sv, ev):
        return True

    # Numeric equivalence: 1781138657473 == 1781138657473.0, "5" == "5.0", "0" == "0.0"
    try:
        fsv, fev = float(sv), float(ev)
        if fsv == fev:
            return True
    except (TypeError, ValueError):
        pass

    if _is_name_like_field(field_path) and (rsv in rev or rev in rsv):
        return True

    return False
