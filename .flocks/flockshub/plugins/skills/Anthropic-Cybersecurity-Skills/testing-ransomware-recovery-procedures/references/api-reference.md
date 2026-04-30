# API Reference: Testing Ransomware Recovery Procedures

## CLI Usage

```bash
# Generate hash manifest for a directory (pre-backup baseline)
python agent.py --hash-dir /data/critical-app -o manifest_baseline.json

# Compare original manifest against restored data
python agent.py --compare manifest_baseline.json manifest_restored.json

# Check if a service is running after restore
python agent.py --check-service postgresql

# Check database connectivity after restore
python agent.py --check-db postgresql:localhost:5432

# Run full recovery drill from config
python agent.py --config drill_config.json -o recovery_report.json
```

## Drill Configuration Format

```json
{
  "systems": [
    {
      "name": "core-database",
      "tier": 1,
      "rto_target_seconds": 3600,
      "rpo_target_seconds": 900,
      "backup_timestamp_epoch": 1711000000,
      "restore_directory": "/restored/core-db",
      "manifest_file": "/manifests/core-db-baseline.json",
      "services": ["postgresql"],
      "database": {
        "type": "postgresql",
        "host": "localhost",
        "port": 5432
      }
    },
    {
      "name": "web-application",
      "tier": 2,
      "rto_target_seconds": 14400,
      "rpo_target_seconds": 3600,
      "restore_directory": "/restored/webapp",
      "services": ["nginx", "gunicorn"]
    }
  ]
}
```

## Recovery Phases Tracked

| Phase | Timestamp Key | Description |
|-------|--------------|-------------|
| Incident Declaration | `incident_declared` | Simulated ransomware detection time |
| Backup Identification | `backup_identified` | Clean restore point located |
| Restore Initiated | `restore_initiated` | Backup restore process started |
| Restore Completed | `restore_completed` | Data fully written to target |
| Service Restored | `service_restored` | Application validated and operational |

## RTO/RPO Calculation

```
Actual RTO = service_restored - incident_declared
Actual RPO = incident_declared - backup_timestamp

RTO Met = Actual RTO <= RTO Target
RPO Met = Actual RPO <= RPO Target
```

## Tier Definitions

| Tier | RTO Range | RPO Range | System Classification |
|------|-----------|-----------|----------------------|
| 1 | < 1 hour | < 15 min | Mission-critical (AD, core DB) |
| 2 | < 4 hours | < 1 hour | Business-critical (ERP, email) |
| 3 | < 24 hours | < 4 hours | Business-operational (file shares) |
| 4 | < 72 hours | < 24 hours | Non-critical (dev/test, analytics) |

## Hash Manifest Format

```json
{
  "config/app.yaml": "a3f2b8c9d1e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0",
  "data/users.db": "1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
  "bin/server": "PERMISSION_DENIED"
}
```

## Validation Checks

| Check | Description | Pass Criteria |
|-------|-------------|---------------|
| file_count | Files present in restored directory | count > 0 |
| integrity_check | Hash comparison vs baseline manifest | No missing or modified files |
| service_* | System service running post-restore | Service status is RUNNING/active |
| database_connectivity | Database port reachable | TCP connection succeeds |

## Report Output Schema

```json
{
  "report_date": "2026-03-19T12:00:00+00:00",
  "drill_type": "ransomware_recovery_validation",
  "systems_tested": 2,
  "systems_meeting_rto": 2,
  "systems_meeting_rpo": 1,
  "overall_pass": false,
  "results": [
    {
      "system_name": "core-database",
      "tier": 1,
      "rto_target_seconds": 3600,
      "actual_rto_seconds": 2400.5,
      "rto_met": true,
      "rpo_met": true,
      "validations": {},
      "errors": []
    }
  ]
}
```

## References

- NIST SP 800-184: Guide for Cybersecurity Event Recovery
- NIST SP 800-34 Rev 1: Contingency Planning Guide
- CISA Ransomware Guide: https://www.cisa.gov/stopransomware
- Veeam Recovery Best Practices: https://www.veeam.com/blog/recovery-time-recovery-point-objectives.html
