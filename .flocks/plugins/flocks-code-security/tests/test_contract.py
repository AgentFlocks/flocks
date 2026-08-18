from __future__ import annotations

from flocks_code_security.contract import finding_identity


def test_finding_identity_matches_codex_security_completed_scan_example() -> None:
    finding_id, occurrence_id, fingerprints = finding_identity(
        "scan_example_001",
        "target_sha256_example",
        "path-traversal.archive-extraction",
        "archive-entry-write-without-containment",
    )

    assert finding_id == "csf_852f90d6e1177502ff113d4a"
    assert occurrence_id == "occ_e79cb19591e696572a1c22be"
    assert fingerprints == {
        "algorithm": "codex-security/v1",
        "primary": (
            "codex-security/v1:sha256:"
            "990a4a6a2ec18440dd47eac4d7256c0ee2c02db1b43104720cab3cbe9db706ca"
        ),
    }
