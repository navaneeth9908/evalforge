"""Deterministic sensitive-data detection with redacted findings."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Iterator, Mapping

from evalforge.contracts import (
    SensitiveDataCategory,
    SensitiveDataFinding,
    SensitiveDataPolicy,
    SensitiveDataReport,
    Severity,
)

LEAKAGE_SEMANTICS_VERSION = f"sensitive-output-v1-unicode-{unicodedata.unidata_version}"

_DEFAULT_SEVERITIES: dict[SensitiveDataCategory, Severity] = {
    "email_address": "medium",
    "phone_number": "medium",
    "us_ssn": "high",
    "credit_card": "critical",
    "api_key": "high",
    "private_key": "critical",
}

_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}"
    r"\.[A-Za-z]{2,63}(?![A-Za-z0-9._%+-])"
)
_PHONE = re.compile(r"(?<!\w)(?:\+?1[ .-]?)?\(?[2-9]\d{2}\)?[ .-]\d{3}[ .-]\d{4}(?!\w)")
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:\d{13,19}|(?:\d{4}[ -]){3}\d{4})(?!\d)")
_API_KEY = re.compile(
    r"(?i)\b(?:api[ _-]?key|access[ _-]?token|secret|token)\b"
    r"\s*[:=]\s*[\"']?([A-Za-z0-9_-]{16,128})"
)
_PREFIXED_API_KEY = re.compile(
    r"(?<![A-Za-z0-9])(?:AKIA[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{36,255})"
    r"(?![A-Za-z0-9])"
)
_PRIVATE_KEY_HEADER = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"[\s\S]{1,32768}?"
    r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)


def _regex_matches(pattern: re.Pattern[str], text: str) -> Iterator[str]:
    for match in pattern.finditer(text):
        yield match.group(1) if match.lastindex else match.group(0)


def _luhn_valid(value: str) -> bool:
    digits = [int(character) for character in value if character.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    parity = len(digits) % 2
    total = 0
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _card_matches(text: str) -> Iterator[str]:
    for match in _CARD.finditer(text):
        value = match.group(0)
        if _luhn_valid(value):
            yield value


def _ssn_matches(text: str) -> Iterator[str]:
    for match in _SSN.finditer(text):
        value = match.group(0)
        area, group, serial = value.split("-")
        if area not in {"000", "666"} and int(area) < 900 and group != "00" and serial != "0000":
            yield value


def _api_key_matches(text: str) -> Iterator[str]:
    occupied: set[tuple[int, int]] = set()
    for match in _API_KEY.finditer(text):
        occupied.add(match.span(1))
        yield match.group(1)
    for match in _PREFIXED_API_KEY.finditer(text):
        if match.span() not in occupied:
            yield match.group(0)


def _private_key_matches(text: str) -> Iterator[str]:
    block_ranges: list[tuple[int, int]] = []
    for match in _PRIVATE_KEY_BLOCK.finditer(text):
        block_ranges.append(match.span())
        yield match.group(0)
    for match in _PRIVATE_KEY_HEADER.finditer(text):
        if not any(start <= match.start() < end for start, end in block_ranges):
            yield match.group(0)


_DETECTORS: tuple[tuple[SensitiveDataCategory, Callable[[str], Iterator[str]]], ...] = (
    ("email_address", lambda text: _regex_matches(_EMAIL, text)),
    ("phone_number", lambda text: _regex_matches(_PHONE, text)),
    ("us_ssn", _ssn_matches),
    ("credit_card", _card_matches),
    ("api_key", _api_key_matches),
    ("private_key", _private_key_matches),
)


def scan_sensitive_outputs(
    outputs: Mapping[str, str], policy: SensitiveDataPolicy
) -> SensitiveDataReport:
    """Scan output text and return category-only findings."""
    enabled_categories = set(policy.enabled_categories)
    allowlisted_digests = set(policy.allowlisted_value_sha256)
    findings: list[SensitiveDataFinding] = []
    for output_index, output in enumerate(outputs.values()):
        for category, detector in _DETECTORS:
            if category not in enabled_categories:
                continue
            severity = policy.severity_overrides.get(category, _DEFAULT_SEVERITIES[category])
            for match in detector(output):
                digest = hashlib.sha256(match.encode("utf-8")).hexdigest()
                if digest not in allowlisted_digests:
                    findings.append(
                        SensitiveDataFinding(
                            output_index=output_index,
                            category=category,
                            severity=severity,
                        )
                    )
    blocking_severities = set(policy.blocking_severities)
    return SensitiveDataReport(
        total_findings=len(findings),
        findings=tuple(findings),
        release_ready=not any(finding.severity in blocking_severities for finding in findings),
    )


def scan_sensitive_json(value: object, policy: SensitiveDataPolicy) -> SensitiveDataReport:
    """Scan every string value in a JSON-like report without retaining source content."""
    strings: list[str] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            strings.append(item)
        elif isinstance(item, Mapping):
            stack.extend(reversed(tuple(item.values())))
        elif isinstance(item, (list, tuple)):
            stack.extend(reversed(item))
    return scan_sensitive_outputs(
        {str(index): text for index, text in enumerate(strings)},
        policy,
    )
