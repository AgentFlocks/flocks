#!/usr/bin/env python3
"""Agent for validating backup integrity for disaster recovery.

Computes cryptographic hashes, compares manifests, detects corruption,
scans for ransomware artifacts, measures file entropy, and validates
backup recoverability.
"""

import argparse
import hashlib
import json
import math
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


RANSOMWARE_EXTENSIONS = {
    ".encrypted", ".locked", ".crypt", ".ransom", ".pay",
    ".wncry", ".wcry", ".cerber", ".locky", ".zepto",
    ".osiris", ".aesir", ".thor", ".odin", ".crypz",
    ".crypted", ".enc", ".crypto", ".lockbit",
}

RANSOM_NOTE_PATTERNS = [
    "README_TO_DECRYPT", "HOW_TO_RECOVER", "DECRYPT_INSTRUCTIONS",
    "HELP_DECRYPT", "RECOVERY_INSTRUCTIONS", "RESTORE_FILES",
    "READ_ME_TO_DECRYPT", "YOUR_FILES_ARE_ENCRYPTED",
    "!README!", "DECRYPT_YOUR_FILES",
]


def compute_file_hash(filepath, algorithm="sha256"):
    """Compute cryptographic hash of a single file."""
    h = hashlib.new(algorithm)
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except (PermissionError, OSError) as e:
        return f"ERROR:{e}"


def generate_manifest(directory, algorithm="sha256"):
    """Generate hash manifest for all files in a directory."""
    manifest = {}
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return {"error": f"Directory not found: {directory}"}

    total = 0
    errors = 0
    for fpath in sorted(dir_path.rglob("*")):
        if fpath.is_file():
            total += 1
            digest = compute_file_hash(str(fpath), algorithm)
            rel = str(fpath.relative_to(dir_path))
            manifest[rel] = digest
            if digest.startswith("ERROR:"):
                errors += 1

    return {
        "directory": str(directory),
        "algorithm": algorithm,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_files": total,
        "errors": errors,
        "hashes": manifest,
    }


def compare_manifests(baseline_path, restored_path):
    """Compare two manifest files to detect integrity issues."""
    with open(baseline_path, "r") as f:
        baseline = json.load(f)
    with open(restored_path, "r") as f:
        restored = json.load(f)

    base_hashes = baseline.get("hashes", baseline)
    rest_hashes = restored.get("hashes", restored)

    missing = []
    modified = []
    added = []

    for fname, base_hash in base_hashes.items():
        if fname not in rest_hashes:
            missing.append(fname)
        elif rest_hashes[fname] != base_hash:
            modified.append({"file": fname, "baseline": base_hash,
                             "restored": rest_hashes[fname]})

    for fname in rest_hashes:
        if fname not in base_hashes:
            added.append(fname)

    integrity_pass = len(missing) == 0 and len(modified) == 0
    return {
        "baseline_files": len(base_hashes),
        "restored_files": len(rest_hashes),
        "missing_files": missing,
        "missing_count": len(missing),
        "modified_files": modified,
        "modified_count": len(modified),
        "added_files": added,
        "added_count": len(added),
        "integrity_pass": integrity_pass,
    }


def calculate_entropy(filepath):
    """Calculate Shannon entropy of a file (0-8 bits per byte)."""
    try:
        with open(filepath, "rb") as f:
            data = f.read()
    except (PermissionError, OSError):
        return None

    if not data:
        return 0.0

    byte_counts = Counter(data)
    length = len(data)
    entropy = 0.0
    for count in byte_counts.values():
        p = count / length
        if p > 0:
            entropy -= p * math.log2(p)
    return round(entropy, 4)


def entropy_scan(directory, threshold=7.9):
    """Scan directory for files with suspiciously high entropy (possible encryption)."""
    suspicious = []
    scanned = 0
    dir_path = Path(directory)

    for fpath in dir_path.rglob("*"):
        if not fpath.is_file():
            continue
        if fpath.stat().st_size < 1024:
            continue
        scanned += 1
        ent = calculate_entropy(str(fpath))
        if ent is not None and ent >= threshold:
            suspicious.append({
                "file": str(fpath.relative_to(dir_path)),
                "entropy": ent,
                "size_bytes": fpath.stat().st_size,
            })

    return {
        "directory": str(directory),
        "threshold": threshold,
        "files_scanned": scanned,
        "suspicious_count": len(suspicious),
        "suspicious_files": suspicious[:100],
    }


def scan_ransomware_artifacts(directory):
    """Scan restored backup for ransomware indicators."""
    findings = {
        "ransomware_extensions": [],
        "ransom_notes": [],
        "total_scanned": 0,
    }
    dir_path = Path(directory)

    for fpath in dir_path.rglob("*"):
        if not fpath.is_file():
            continue
        findings["total_scanned"] += 1

        if fpath.suffix.lower() in RANSOMWARE_EXTENSIONS:
            findings["ransomware_extensions"].append(
                str(fpath.relative_to(dir_path))
            )

        for pattern in RANSOM_NOTE_PATTERNS:
            if pattern.lower() in fpath.name.lower():
                findings["ransom_notes"].append(
                    str(fpath.relative_to(dir_path))
                )
                break

    findings["clean"] = (
        len(findings["ransomware_extensions"]) == 0
        and len(findings["ransom_notes"]) == 0
    )
    return findings


def validate_backup(directory, baseline_manifest=None, check_ransomware=True,
                    check_entropy=True, entropy_threshold=7.9):
    """Run full backup validation suite."""
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "directory": str(directory),
        "checks": {},
    }

    # File count and size
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return {"error": f"Directory not found: {directory}"}

    total_files = sum(1 for _ in dir_path.rglob("*") if _.is_file())
    total_size = sum(f.stat().st_size for f in dir_path.rglob("*") if f.is_file())
    results["checks"]["file_stats"] = {
        "total_files": total_files,
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "pass": total_files > 0,
    }

    # Manifest comparison
    if baseline_manifest and os.path.isfile(baseline_manifest):
        current = generate_manifest(directory)
        current_path = str(dir_path / ".current_manifest.json")
        with open(current_path, "w") as f:
            json.dump(current, f)
        comparison = compare_manifests(baseline_manifest, current_path)
        results["checks"]["integrity"] = comparison
        os.remove(current_path)
    else:
        results["checks"]["integrity"] = {"skipped": True,
                                           "reason": "No baseline manifest provided"}

    # Ransomware artifact scan
    if check_ransomware:
        results["checks"]["ransomware_scan"] = scan_ransomware_artifacts(directory)

    # Entropy scan
    if check_entropy:
        results["checks"]["entropy_scan"] = entropy_scan(directory, entropy_threshold)

    # Overall verdict
    checks = results["checks"]
    results["overall_pass"] = (
        checks.get("file_stats", {}).get("pass", False)
        and checks.get("integrity", {}).get("integrity_pass", True)
        and checks.get("ransomware_scan", {}).get("clean", True)
        and checks.get("entropy_scan", {}).get("suspicious_count", 0) == 0
    )

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Backup Integrity Validation Agent"
    )
    parser.add_argument("--generate-manifest",
                        help="Generate hash manifest for a directory")
    parser.add_argument("--compare", nargs=2, metavar=("BASELINE", "RESTORED"),
                        help="Compare two manifest JSON files")
    parser.add_argument("--validate", help="Run full validation on a backup directory")
    parser.add_argument("--baseline", help="Baseline manifest for comparison")
    parser.add_argument("--entropy-scan", help="Scan directory for high-entropy files")
    parser.add_argument("--entropy-threshold", type=float, default=7.9,
                        help="Entropy threshold (default: 7.9)")
    parser.add_argument("--ransomware-scan",
                        help="Scan directory for ransomware artifacts")
    parser.add_argument("--algorithm", default="sha256",
                        choices=["sha256", "sha512", "sha3_256", "blake2b"],
                        help="Hash algorithm (default: sha256)")
    parser.add_argument("--output", "-o", help="Output file path")
    args = parser.parse_args()

    print("[*] Backup Integrity Validation Agent")
    result = None

    if args.generate_manifest:
        result = generate_manifest(args.generate_manifest, args.algorithm)
        print(f"[*] Generated manifest: {result.get('total_files', 0)} files")

    elif args.compare:
        result = compare_manifests(args.compare[0], args.compare[1])
        status = "PASS" if result["integrity_pass"] else "FAIL"
        print(f"[*] Integrity check: {status}")
        if result["missing_count"]:
            print(f"[!] Missing files: {result['missing_count']}")
        if result["modified_count"]:
            print(f"[!] Modified files: {result['modified_count']}")

    elif args.validate:
        result = validate_backup(
            args.validate,
            baseline_manifest=args.baseline,
            entropy_threshold=args.entropy_threshold,
        )
        status = "PASS" if result.get("overall_pass") else "FAIL"
        print(f"[*] Overall validation: {status}")

    elif args.entropy_scan:
        result = entropy_scan(args.entropy_scan, args.entropy_threshold)
        print(f"[*] Scanned {result['files_scanned']} files, "
              f"{result['suspicious_count']} suspicious")

    elif args.ransomware_scan:
        result = scan_ransomware_artifacts(args.ransomware_scan)
        status = "CLEAN" if result["clean"] else "INFECTED"
        print(f"[*] Ransomware scan: {status}")

    else:
        parser.print_help()
        return

    if result:
        output = json.dumps(result, indent=2)
        if args.output:
            with open(args.output, "w") as f:
                f.write(output)
            print(f"[*] Results saved to {args.output}")
        else:
            print(output)


if __name__ == "__main__":
    main()
