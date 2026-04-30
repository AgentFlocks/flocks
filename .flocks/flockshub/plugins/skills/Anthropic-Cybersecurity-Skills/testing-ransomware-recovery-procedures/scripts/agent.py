#!/usr/bin/env python3
"""Agent for testing and validating ransomware recovery procedures.

Measures RTO/RPO against targets, validates backup restore integrity,
tracks recovery sequencing, and generates compliance reports.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


class RecoveryTest:
    """Represents a single system recovery test with timing and validation."""

    def __init__(self, system_name, tier, rto_target_seconds, rpo_target_seconds):
        self.system_name = system_name
        self.tier = tier
        self.rto_target = rto_target_seconds
        self.rpo_target = rpo_target_seconds
        self.timestamps = {}
        self.validations = {}
        self.errors = []

    def mark(self, phase):
        """Record a timestamp for a recovery phase."""
        self.timestamps[phase] = time.time()

    def validate(self, check_name, passed, detail=""):
        """Record a validation result."""
        self.validations[check_name] = {"passed": passed, "detail": detail}

    def actual_rto(self):
        """Calculate actual RTO from incident declaration to service restored."""
        t0 = self.timestamps.get("incident_declared")
        t4 = self.timestamps.get("service_restored")
        if t0 and t4:
            return t4 - t0
        return None

    def actual_rpo(self, backup_timestamp_epoch):
        """Calculate actual RPO from last backup to incident declaration."""
        t0 = self.timestamps.get("incident_declared")
        if t0 and backup_timestamp_epoch:
            return t0 - backup_timestamp_epoch
        return None

    def to_dict(self, backup_timestamp_epoch=None):
        rto = self.actual_rto()
        rpo = self.actual_rpo(backup_timestamp_epoch)
        return {
            "system_name": self.system_name,
            "tier": self.tier,
            "rto_target_seconds": self.rto_target,
            "rpo_target_seconds": self.rpo_target,
            "actual_rto_seconds": round(rto, 2) if rto else None,
            "actual_rpo_seconds": round(rpo, 2) if rpo else None,
            "rto_met": rto <= self.rto_target if rto else None,
            "rpo_met": rpo <= self.rpo_target if rpo else None,
            "timestamps": {
                k: datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
                for k, v in self.timestamps.items()
            },
            "validations": self.validations,
            "errors": self.errors,
        }


def compute_file_hashes(directory, algorithm="sha256"):
    """Compute hashes for all files in a directory for integrity verification."""
    hashes = {}
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return {"error": f"Directory not found: {directory}"}

    for fpath in sorted(dir_path.rglob("*")):
        if fpath.is_file():
            h = hashlib.new(algorithm)
            try:
                with open(fpath, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
                rel = str(fpath.relative_to(dir_path))
                hashes[rel] = h.hexdigest()
            except PermissionError:
                hashes[str(fpath.relative_to(dir_path))] = "PERMISSION_DENIED"
    return hashes


def compare_manifests(original_manifest, restored_manifest):
    """Compare two hash manifests to detect missing, added, or changed files."""
    missing = []
    modified = []
    added = []

    for fname, orig_hash in original_manifest.items():
        if fname not in restored_manifest:
            missing.append(fname)
        elif restored_manifest[fname] != orig_hash:
            modified.append(fname)

    for fname in restored_manifest:
        if fname not in original_manifest:
            added.append(fname)

    return {
        "total_original": len(original_manifest),
        "total_restored": len(restored_manifest),
        "missing_files": missing,
        "modified_files": modified,
        "added_files": added,
        "integrity_pass": len(missing) == 0 and len(modified) == 0,
    }


def check_service_health(service_name):
    """Check if a service is running and responsive."""
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["sc", "query", service_name],
                capture_output=True, text=True, timeout=10
            )
            running = "RUNNING" in result.stdout
            return {"service": service_name, "running": running, "platform": "windows"}
        except (subprocess.SubprocessError, FileNotFoundError):
            return {"service": service_name, "running": False, "error": "check failed"}
    else:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", service_name],
                capture_output=True, text=True, timeout=10
            )
            active = result.stdout.strip() == "active"
            return {"service": service_name, "running": active, "platform": "linux"}
        except (subprocess.SubprocessError, FileNotFoundError):
            return {"service": service_name, "running": False, "error": "check failed"}


def check_database_connectivity(db_type, host="localhost", port=None):
    """Verify database is accessible after restore."""
    ports = {"postgresql": 5432, "mysql": 3306, "mssql": 1433}
    port = port or ports.get(db_type, 5432)

    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        result = sock.connect_ex((host, port))
        return {
            "database": db_type,
            "host": host,
            "port": port,
            "reachable": result == 0,
        }
    except socket.error as e:
        return {"database": db_type, "host": host, "port": port, "reachable": False,
                "error": str(e)}
    finally:
        sock.close()


def run_recovery_drill(config):
    """Execute a recovery drill based on a configuration dict."""
    results = []

    for system in config.get("systems", []):
        test = RecoveryTest(
            system_name=system["name"],
            tier=system.get("tier", 3),
            rto_target_seconds=system.get("rto_target_seconds", 14400),
            rpo_target_seconds=system.get("rpo_target_seconds", 3600),
        )

        test.mark("incident_declared")
        print(f"[*] Recovery drill started for: {system['name']}")

        # Phase: Locate backup
        test.mark("backup_identified")
        backup_ts = system.get("backup_timestamp_epoch", time.time() - 3600)

        # Phase: Validate restore directory if provided
        restore_dir = system.get("restore_directory")
        if restore_dir and os.path.isdir(restore_dir):
            test.mark("restore_initiated")
            hashes = compute_file_hashes(restore_dir)
            file_count = len([v for v in hashes.values() if v != "PERMISSION_DENIED"])
            test.validate("file_count", file_count > 0,
                          f"{file_count} files found in restored directory")
            test.mark("restore_completed")

            # Compare with manifest if provided
            manifest_path = system.get("manifest_file")
            if manifest_path and os.path.isfile(manifest_path):
                with open(manifest_path, "r") as f:
                    original_manifest = json.load(f)
                comparison = compare_manifests(original_manifest, hashes)
                test.validate("integrity_check", comparison["integrity_pass"],
                              json.dumps(comparison, indent=2))
        else:
            test.validate("restore_directory", False,
                          f"Directory not found: {restore_dir}")

        # Phase: Check services
        for svc in system.get("services", []):
            health = check_service_health(svc)
            test.validate(f"service_{svc}", health.get("running", False),
                          json.dumps(health))

        # Phase: Check database
        db = system.get("database")
        if db:
            db_check = check_database_connectivity(
                db.get("type", "postgresql"),
                db.get("host", "localhost"),
                db.get("port"),
            )
            test.validate("database_connectivity", db_check["reachable"],
                          json.dumps(db_check))

        test.mark("service_restored")
        results.append(test.to_dict(backup_ts))
        print(f"[*] Recovery drill completed for: {system['name']}")

    return results


def generate_report(results, output_path=None):
    """Generate a recovery test report."""
    report = {
        "report_date": datetime.now(timezone.utc).isoformat(),
        "drill_type": "ransomware_recovery_validation",
        "systems_tested": len(results),
        "systems_meeting_rto": sum(1 for r in results if r.get("rto_met")),
        "systems_meeting_rpo": sum(1 for r in results if r.get("rpo_met")),
        "overall_pass": all(
            r.get("rto_met") and r.get("rpo_met") for r in results
            if r.get("rto_met") is not None
        ),
        "results": results,
    }

    if output_path:
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"[*] Report saved to {output_path}")

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Ransomware Recovery Procedure Testing Agent"
    )
    parser.add_argument("--config", help="JSON config file for recovery drill")
    parser.add_argument("--hash-dir", help="Compute file hashes for a directory")
    parser.add_argument("--compare", nargs=2, metavar=("ORIGINAL", "RESTORED"),
                        help="Compare two hash manifest JSON files")
    parser.add_argument("--check-service", help="Check if a system service is running")
    parser.add_argument("--check-db", help="Check database connectivity (type:host:port)")
    parser.add_argument("--output", "-o", help="Output report file path")
    args = parser.parse_args()

    print("[*] Ransomware Recovery Procedure Testing Agent")

    if args.hash_dir:
        hashes = compute_file_hashes(args.hash_dir)
        print(json.dumps(hashes, indent=2))
        if args.output:
            with open(args.output, "w") as f:
                json.dump(hashes, f, indent=2)
            print(f"[*] Hash manifest saved to {args.output}")
        return

    if args.compare:
        with open(args.compare[0], "r") as f:
            orig = json.load(f)
        with open(args.compare[1], "r") as f:
            restored = json.load(f)
        result = compare_manifests(orig, restored)
        print(json.dumps(result, indent=2))
        return

    if args.check_service:
        result = check_service_health(args.check_service)
        print(json.dumps(result, indent=2))
        return

    if args.check_db:
        parts = args.check_db.split(":")
        db_type = parts[0]
        host = parts[1] if len(parts) > 1 else "localhost"
        port = int(parts[2]) if len(parts) > 2 else None
        result = check_database_connectivity(db_type, host, port)
        print(json.dumps(result, indent=2))
        return

    if args.config:
        with open(args.config, "r") as f:
            config = json.load(f)
        results = run_recovery_drill(config)
        report = generate_report(results, args.output)
        print(json.dumps(report, indent=2))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
