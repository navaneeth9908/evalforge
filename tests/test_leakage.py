from __future__ import annotations


def test_detects_common_sensitive_categories_without_retaining_matches() -> None:
    from evalforge.contracts import SensitiveDataPolicy
    from evalforge.leakage import scan_sensitive_outputs

    synthetic_values = {
        "email": "person@example.invalid",
        "phone": "202-555-0100",
        "ssn": "123-45-6789",
        "card": "4242 4242 4242 4242",
        "key": "SYNTHETIC_API_TOKEN_1234567890",
        "private_key": "-----BEGIN PRIVATE KEY-----",
    }
    output = (
        f"email {synthetic_values['email']} phone {synthetic_values['phone']} "
        f"ssn {synthetic_values['ssn']} card {synthetic_values['card']} "
        f"api_key={synthetic_values['key']} {synthetic_values['private_key']}"
    )
    policy = SensitiveDataPolicy(schema_version=1)

    report = scan_sensitive_outputs({"synthetic-case": output}, policy)

    assert [finding.category for finding in report.findings] == [
        "email_address",
        "phone_number",
        "us_ssn",
        "credit_card",
        "api_key",
        "private_key",
    ]
    assert all(finding.output_index == 0 for finding in report.findings)
    assert report.total_findings == 6
    serialized = report.model_dump_json()
    assert all(value not in serialized for value in synthetic_values.values())


def test_policy_disables_categories_and_allowlists_exact_synthetic_values() -> None:
    import hashlib

    from evalforge.contracts import SensitiveDataPolicy
    from evalforge.leakage import scan_sensitive_outputs

    allowed = "allowed@example.invalid"
    blocked = "blocked@example.invalid"
    policy = SensitiveDataPolicy(
        schema_version=1,
        enabled_categories=("email_address",),
        allowlisted_value_sha256=(hashlib.sha256(allowed.encode("utf-8")).hexdigest(),),
    )

    report = scan_sensitive_outputs(
        {"case-a": f"{allowed} {blocked} api_key=SYNTHETIC_TOKEN_123456789"}, policy
    )

    assert [(finding.output_index, finding.category) for finding in report.findings] == [
        (0, "email_address")
    ]
    serialized = report.model_dump_json()
    assert allowed not in serialized
    assert blocked not in serialized


def test_severity_overrides_control_release_gate() -> None:
    from evalforge.contracts import SensitiveDataPolicy
    from evalforge.leakage import scan_sensitive_outputs

    output = {"synthetic-case": "api_key=SYNTHETIC_TOKEN_123456789"}
    nonblocking = SensitiveDataPolicy(
        schema_version=1,
        enabled_categories=("api_key",),
        severity_overrides={"api_key": "low"},
        blocking_severities=("high", "critical"),
    )
    blocking = SensitiveDataPolicy(
        schema_version=1,
        enabled_categories=("api_key",),
        blocking_severities=("high", "critical"),
    )

    nonblocking_report = scan_sensitive_outputs(output, nonblocking)
    blocking_report = scan_sensitive_outputs(output, blocking)

    assert nonblocking_report.findings[0].severity == "low"
    assert nonblocking_report.release_ready is True
    assert blocking_report.findings[0].severity == "high"
    assert blocking_report.release_ready is False


def test_structurally_invalid_identifiers_do_not_create_false_positives() -> None:
    from evalforge.contracts import SensitiveDataPolicy
    from evalforge.leakage import scan_sensitive_outputs

    output = {
        "synthetic-case": (
            "invalid SSNs 000-12-3456 666-12-3456 900-12-3456 "
            "123-00-3456 123-45-0000 and invalid card 4242 4242 4242 4241"
        )
    }
    policy = SensitiveDataPolicy(
        schema_version=1,
        enabled_categories=("us_ssn", "credit_card"),
    )

    report = scan_sensitive_outputs(output, policy)

    assert report.findings == ()
    assert report.release_ready is True


def test_private_key_allowlist_applies_to_one_exact_synthetic_block() -> None:
    import hashlib

    from evalforge.contracts import SensitiveDataPolicy
    from evalforge.leakage import scan_sensitive_outputs

    allowed = (
        "-----BEGIN PRIVATE KEY-----\nSYNTHETIC_ALLOWED_PLACEHOLDER\n-----END PRIVATE KEY-----"
    )
    blocked = (
        "-----BEGIN PRIVATE KEY-----\nSYNTHETIC_BLOCKED_PLACEHOLDER\n-----END PRIVATE KEY-----"
    )
    policy = SensitiveDataPolicy(
        schema_version=1,
        enabled_categories=("private_key",),
        allowlisted_value_sha256=(hashlib.sha256(allowed.encode("utf-8")).hexdigest(),),
    )

    report = scan_sensitive_outputs({"synthetic-case": f"{allowed}\n{blocked}"}, policy)

    assert len(report.findings) == 1
    assert report.findings[0].category == "private_key"
    serialized = report.model_dump_json()
    assert "SYNTHETIC_ALLOWED_PLACEHOLDER" not in serialized
    assert "SYNTHETIC_BLOCKED_PLACEHOLDER" not in serialized


def test_recognizes_common_prefixed_secret_placeholders_without_labels() -> None:
    from evalforge.contracts import SensitiveDataPolicy
    from evalforge.leakage import scan_sensitive_outputs

    aws_shaped = "AKIAABCDEFGHIJKLMNOP"
    github_shaped = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
    policy = SensitiveDataPolicy(schema_version=1, enabled_categories=("api_key",))

    report = scan_sensitive_outputs({"synthetic-case": f"{aws_shaped} {github_shaped}"}, policy)

    assert [finding.category for finding in report.findings] == ["api_key", "api_key"]
    serialized = report.model_dump_json()
    assert aws_shaped not in serialized
    assert github_shaped not in serialized


def test_findings_do_not_retain_caller_supplied_output_identifiers() -> None:
    from evalforge.contracts import SensitiveDataPolicy
    from evalforge.leakage import scan_sensitive_outputs

    sensitive_identifier = "case-owner@example.invalid"
    policy = SensitiveDataPolicy(schema_version=1, enabled_categories=("api_key",))

    report = scan_sensitive_outputs(
        {sensitive_identifier: "api_key=SYNTHETIC_TOKEN_123456789"}, policy
    )

    assert report.findings[0].output_index == 0
    assert sensitive_identifier not in report.model_dump_json()
