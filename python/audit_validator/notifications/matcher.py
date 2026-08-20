"""Notification string assertion and regex matching engine."""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class MatchResult:
    is_match: bool
    expected: str
    actual: str
    match_type: str  # "exact", "substring", "regex", "none"
    diff: str = ""
    error_message: str = ""


def normalize_whitespace(text: str) -> str:
    """Replace multiple consecutive spaces/newlines with a single space."""
    return re.sub(r"\s+", " ", text).strip()


def assert_notification_text(
    actual_text: str,
    expected_text_or_regex: str,
    *,
    is_regex: bool = False,
    ignore_case: bool = True,
) -> MatchResult:
    """
    Compare actual notification text against expected string or regex.
    Provides human-readable diff if matching fails.
    """
    norm_actual = normalize_whitespace(actual_text)
    norm_expected = normalize_whitespace(expected_text_or_regex)

    flags = re.IGNORECASE if ignore_case else 0

    if is_regex:
        try:
            pattern = re.compile(norm_expected, flags)
            if pattern.search(norm_actual):
                return MatchResult(
                    is_match=True,
                    expected=norm_expected,
                    actual=norm_actual,
                    match_type="regex",
                )
        except re.error as e:
            return MatchResult(
                is_match=False,
                expected=norm_expected,
                actual=norm_actual,
                match_type="none",
                error_message=f"Invalid regex pattern: {e}",
            )

    # Direct / Substring Check
    if ignore_case:
        exact = norm_actual.lower() == norm_expected.lower()
        substring = norm_expected.lower() in norm_actual.lower()
    else:
        exact = norm_actual == norm_expected
        substring = norm_expected in norm_actual

    if exact:
        return MatchResult(
            is_match=True,
            expected=norm_expected,
            actual=norm_actual,
            match_type="exact",
        )
    elif substring:
        return MatchResult(
            is_match=True,
            expected=norm_expected,
            actual=norm_actual,
            match_type="substring",
        )

    # Compute line-by-line / word diff
    diff_lines = list(
        difflib.unified_diff(
            norm_expected.splitlines(keepends=True),
            norm_actual.splitlines(keepends=True),
            fromfile="expected",
            tofile="actual",
        )
    )
    diff_str = "".join(diff_lines)

    return MatchResult(
        is_match=False,
        expected=norm_expected,
        actual=norm_actual,
        match_type="none",
        diff=diff_str or f"- Expected: {norm_expected}\n+ Actual:   {norm_actual}",
        error_message="Received text did not match expected pattern or string.",
    )
