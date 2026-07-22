# API Reference: Validating Backup Integrity for Recovery

## CLI Usage

```bash
# Generate SHA-256 hash manifest for a directory
python agent.py --generate-manifest /data/production -o manifest.json

# Generate manifest with SHA-512
python agent.py --generate-manifest /data/production --algorithm sha512 -o manifest.json

# Compare baseline vs restored manifest
python agent.py --compare baseline_manifest.json restored_manifest.json

# Run full backup validation suite
python agent.py --validate /restore-test --baseline baseline_manifest.json -o report.json

# Scan for ransomware artifacts in restored data
python agent.py --ransomware-scan /restore-test

# Scan for high-entropy (possibly encrypted) files
python agent.py --entropy-scan /restore-test --entropy-threshold 7.9
```

## Hash Algorithms Supported

| Algorithm | Digest Size | Use Case |
|-----------|-------------|----------|
| sha256 | 256 bits | Default; standard integrity verification |
| sha512 | 512 bits | Higher security; larger files |
| sha3_256 | 256 bits | NIST post-quantum recommendation |
| blake2b | 512 bits | Faster alternative; high performance |

## Manifest Format

```json
{
  "directory": "/data/production",
  "algorithm": "sha256",
  "generated_at": "2026-03-19T04:00:00+00:00",
  "total_files": 1523,
  "errors": 0,
  "hashes": {
    "config/app.yaml": "a3f2b8c9d1e4f5a6...",
    "data/users.db": "1b2c3d4e5f6a7b8c...",
    "logs/access.log": "ERROR:Permission denied"
  }
}
```

## Comparison Result Format

```json
{
  "baseline_files": 1523,
  "restored_files": 1520,
  "missing_files": ["logs/audit.log", "tmp/cache.db", "data/session.bin"],
  "missing_count": 3,
  "modified_files": [
    {
      "file": "config/app.yaml",
      "baseline": "a3f2b8c9...",
      "restored": "7e8f9a0b..."
    }
  ],
  "modified_count": 1,
  "added_files": [],
  "added_count": 0,
  "integrity_pass": false
}
```

## Entropy Scan Output

```json
{
  "directory": "/restore-test",
  "threshold": 7.9,
  "files_scanned": 1200,
  "suspicious_count": 3,
  "suspicious_files": [
    {
      "file": "data/report.docx.encrypted",
      "entropy": 7.98,
      "size_bytes": 524288
    }
  ]
}
```

## Entropy Reference Values

| Entropy Range | Interpretation |
|--------------|----------------|
| 0.0 - 1.0 | Highly repetitive data (empty files, padding) |
| 1.0 - 5.0 | Structured text (config files, logs, source code) |
| 5.0 - 7.0 | Binary data (executables, images, databases) |
| 7.0 - 7.8 | Compressed data (zip, gzip, jpg) |
| 7.8 - 8.0 | Encrypted or fully random data (ransomware indicator) |

## Ransomware Scan Output

```json
{
  "ransomware_extensions": [
    "documents/report.docx.locked",
    "data/backup.sql.encrypted"
  ],
  "ransom_notes": [
    "HOW_TO_RECOVER_YOUR_FILES.txt"
  ],
  "total_scanned": 1523,
  "clean": false
}
```

## Known Ransomware Extensions Detected

`.encrypted`, `.locked`, `.crypt`, `.ransom`, `.pay`, `.wncry`, `.wcry`,
`.cerber`, `.locky`, `.zepto`, `.osiris`, `.aesir`, `.thor`, `.odin`,
`.crypz`, `.crypted`, `.enc`, `.crypto`, `.lockbit`

## Full Validation Report Schema

```json
{
  "timestamp": "2026-03-19T04:30:00+00:00",
  "directory": "/restore-test",
  "checks": {
    "file_stats": {
      "total_files": 1523,
      "total_size_bytes": 1073741824,
      "total_size_mb": 1024.0,
      "pass": true
    },
    "integrity": {
      "integrity_pass": true,
      "missing_count": 0,
      "modified_count": 0
    },
    "ransomware_scan": {
      "clean": true,
      "total_scanned": 1523
    },
    "entropy_scan": {
      "files_scanned": 1200,
      "suspicious_count": 0
    }
  },
  "overall_pass": true
}
```

## References

- NIST SP 800-184: Guide for Cybersecurity Event Recovery
- NIST CSF 2.0 RC.RP-03: Backup Integrity Verification
- CIS Controls v8: Control 11 - Data Recovery
- Restic Documentation: https://restic.readthedocs.io/en/stable/045_working_with_repos.html
- BorgBackup Verification: https://borgbackup.readthedocs.io/en/stable/usage/check.html
