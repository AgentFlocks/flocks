---
name: performing-deception-technology-deployment
description: 'Deploys deception technology including honeypots, honeytokens, and decoy systems to detect attackers who have
  bypassed perimeter defenses, providing high-fidelity alerts with near-zero false positive rates. Use when SOC teams need
  early warning of lateral movement, credential abuse, or internal reconnaissance by deploying convincing traps across the
  network.

  '
domain: cybersecurity
subdomain: soc-operations
tags:
- soc
- deception
- honeypot
- honeytoken
- decoy
- lateral-movement
- detection
version: '1.0'
author: mahipal
license: Apache-2.0
nist_csf:
- DE.CM-01
- DE.AE-02
- RS.MA-01
- DE.AE-06
---
# Performing Deception Technology Deployment

## When to Use

Use this skill when:
- SOC teams need high-fidelity detection of post-compromise lateral movement with near-zero false positives
- Existing detection tools miss advanced attackers who avoid triggering threshold-based alerts
- The organization wants to detect credential abuse by planting fake credentials as honeytokens
- Network segmentation gaps need compensating detection controls

**Do not use** as a replacement for fundamental security controls (patching, EDR, network segmentation) — deception is a detection layer, not a prevention mechanism.

## Prerequisites

- Network segments identified for honeypot/decoy deployment (server VLANs, DMZ, OT networks)
- Deception platform (Thinkst Decoy, Attivo/SentinelOne Hologram, or open-source alternatives)
- SIEM integration for deception alerts (any interaction with deception assets is suspicious)
- Active Directory access for honeytoken account and credential creation
- Network team coordination for IP allocation and traffic routing

## Workflow

### Step 1: Map Attack Surface for Deception Placement

Identify high-value network segments where attackers would traverse:

```
DECEPTION DEPLOYMENT MAP
━━━━━━━━━━━━━━━━━━━━━━━━
Segment              Decoy Type          Rationale
Server VLAN          Fake file server    Attackers enumerate SMB shares during recon
Database VLAN        Fake DB server      SQL scanning detected in past incidents
AD/DC Segment        Honeytoken account  Credential theft detection
Executive Subnet     Fake workstation    Targeted attacks pivot through exec systems
DMZ                  Honeypot web app    External attacker detection
OT Network           Fake PLC/HMI        Industrial threat detection
Cloud (AWS VPC)      Decoy EC2 + S3     Cloud lateral movement detection
```

### Step 2: Deploy Thinkst Decoy Devices

Configure Decoy devices mimicking real infrastructure:

**Windows File Server Decoy:**
```json
{
  "device_name": "FILESERVER-BK04",
  "personality": "windows-server-2019",
  "services": {
    "smb": {
      "enabled": true,
      "shares": ["Finance_Backup", "HR_Archive", "IT_Docs"],
      "files": [
        {"name": "Q4_Revenue_2024.xlsx", "alert_on": "read"},
        {"name": "employee_ssn_export.csv", "alert_on": "read"},
        {"name": "admin_passwords.kdbx", "alert_on": "read"}
      ]
    },
    "rdp": {"enabled": true},
    "http": {"enabled": false}
  },
  "network": {
    "ip": "10.0.5.200",
    "hostname": "FILESERVER-BK04",
    "domain": "company.local"
  },
  "alert_webhook": "https://soar.company.com/api/webhook/decoy"
}
```

**Database Server Decoy:**
```json
{
  "device_name": "DB-ARCHIVE-02",
  "personality": "linux-mysql",
  "services": {
    "mysql": {
      "enabled": true,
      "port": 3306,
      "databases": ["customer_pii", "payment_archive"],
      "alert_on_login_attempt": true
    },
    "ssh": {
      "enabled": true,
      "port": 22,
      "alert_on_login_attempt": true
    }
  },
  "network": {
    "ip": "10.0.10.50",
    "hostname": "db-archive-02"
  }
}
```

### Step 3: Deploy Honeytokens in Active Directory

Create fake privileged accounts that should never be used:

```powershell
# Create honeytoken service account
New-ADUser -Name "svc_sql_backup" `
    -SamAccountName "svc_sql_backup" `
    -UserPrincipalName "svc_sql_backup@company.local" `
    -Description "SQL Backup Service Account - DO NOT DELETE" `
    -AccountPassword (ConvertTo-SecureString "FakeP@ssw0rd2024!" -AsPlainText -Force) `
    -Enabled $true `
    -PasswordNeverExpires $true `
    -CannotChangePassword $true

# Add to a group that looks attractive (but monitor for any use)
Add-ADGroupMember -Identity "Domain Admins" -Members "svc_sql_backup"

# Place cached credentials on decoy workstation
# (Mimikatz/credential dumping will find these)
cmdkey /add:fileserver-bk04.company.local /user:company\svc_sql_backup /pass:FakeP@ssw0rd2024!
```

**Monitor honeytoken usage in Splunk:**
```spl
index=wineventlog sourcetype="WinEventLog:Security"
(EventCode=4624 OR EventCode=4625 OR EventCode=4648 OR EventCode=4768 OR EventCode=4769)
TargetUserName="svc_sql_backup"
| eval alert_severity = "CRITICAL"
| eval alert_message = "HONEYTOKEN ACCOUNT USED — Likely credential theft detected"
| table _time, EventCode, src_ip, ComputerName, TargetUserName, Logon_Type, alert_message
```

### Step 4: Deploy Decoy Files and Documents

Plant tracked documents that beacon when opened:

**Decoy Document (Word doc with tracking):**
```python
# Using Thinkst Decoy API to create a decoy token document
import requests

response = requests.post(
    "https://YOURCOMPANY.decoy.tools/api/v1/decoytoken/create",
    data={
        "auth_token": "YOUR_API_TOKEN",
        "kind": "doc-msword",
        "memo": "Finance backup folder decoy document",
        "flock_id": "flock:default"
    }
)
token = response.json()
download_url = token["decoytoken"]["decoytoken_url"]
print(f"Download decoy doc: {download_url}")
# Place this document in honeypot SMB shares and sensitive directories
```

**AWS Decoy Token (S3 access key):**
```python
# Create AWS decoy token — alerts when access key is used
response = requests.post(
    "https://YOURCOMPANY.decoy.tools/api/v1/decoytoken/create",
    data={
        "auth_token": "YOUR_API_TOKEN",
        "kind": "aws-id",
        "memo": "Decoy AWS key in developer laptop .aws/credentials"
    }
)
aws_keys = response.json()
print(f"Access Key: {aws_keys['decoytoken']['access_key_id']}")
print(f"Secret Key: {aws_keys['decoytoken']['secret_access_key']}")
# Plant in .aws/credentials on developer workstations
```

### Step 5: Integrate Deception Alerts with SIEM/SOAR

All deception alerts are high-fidelity — any interaction is suspicious:

**Splunk Alert for Decoy Triggers:**
```spl
index=decoy sourcetype="decoy:alerts"
| eval severity = "CRITICAL"
| eval confidence = "HIGH — Deception asset triggered, zero false positive expected"
| table _time, decoy_name, alert_type, source_ip, service, details
| sendalert create_notable param.rule_title="Deception Alert — Decoy Triggered"
  param.severity="critical" param.drilldown_search="index=decoy source_ip=$source_ip$"
```

**SOAR Automated Response:**
```python
def decoy_triggered(container):
    """Auto-response for deception alerts — high confidence, no approval needed"""
    source_ip = container["artifacts"][0]["cef"]["sourceAddress"]

    # Immediately isolate the source
    phantom.act("quarantine device",
                parameters=[{"ip_hostname": source_ip}],
                assets=["crowdstrike_prod"],
                name="isolate_attacker_host")

    # Block at firewall
    phantom.act("block ip",
                parameters=[{"ip": source_ip, "direction": "both"}],
                assets=["palo_alto_prod"],
                name="block_attacker_ip")

    # Create high-priority incident
    phantom.act("create ticket",
                parameters=[{
                    "short_description": f"DECEPTION ALERT: Decoy triggered from {source_ip}",
                    "urgency": "1",
                    "impact": "1"
                }],
                assets=["servicenow_prod"])

    phantom.set_severity(container, "critical")
```

### Step 6: Maintain Deception Realism

Regularly update decoys to maintain believability:

- Rotate honeytoken passwords quarterly (update cached credentials on decoy workstations)
- Update decoy file modification dates to appear recently accessed
- Add realistic network traffic to honeypots (scheduled SMB enumeration, DNS lookups)
- Register honeypot hostnames in DNS and Active Directory to appear in network scans
- Update decoy document contents to match current business context

## Key Concepts

| Term | Definition |
|------|-----------|
| **Honeypot** | Decoy system mimicking real infrastructure to attract and detect attackers in the network |
| **Honeytoken** | Fake credential, file, or data record that triggers an alert when accessed or used |
| **Decoy** | Lightweight deception device or token that alerts on any interaction (Thinkst Decoy platform) |
| **Breadcrumb** | Planted artifact (cached credential, bookmark, config file) leading attackers to deception assets |
| **High-Fidelity Alert** | Detection signal with near-zero false positive rate because no legitimate user should interact with deception assets |
| **Decoy Network** | Set of interconnected honeypots simulating a realistic network segment to observe attacker TTPs |

## Tools & Systems

- **Thinkst Decoy**: Commercial deception platform offering hardware/virtual canaries and decoy tokens
- **Decoytokens.org**: Free honeytoken generation service (DNS, HTTP, AWS keys, Word docs, SQL queries)
- **Attivo Networks (SentinelOne)**: Enterprise deception platform with AD decoys and endpoint breadcrumbs
- **HoneyDB**: Community honeypot data aggregation platform for threat intelligence sharing
- **T-Pot**: Open-source multi-honeypot platform combining 20+ honeypot types in a Docker deployment

## Common Scenarios

- **Lateral Movement Detection**: Attacker enumerates SMB shares and accesses honeypot file server — immediate high-fidelity alert
- **Credential Theft Discovery**: Mimikatz dumps honeytoken cached credentials — usage of fake account triggers alert
- **Cloud Key Compromise**: Stolen AWS decoy token used from external IP — detects supply chain or insider compromise
- **Ransomware Early Warning**: Ransomware encrypts decoy files on honeypot shares — early detection before production systems affected
- **Insider Threat Signal**: Employee accesses honeypot "salary database" — indicates unauthorized data exploration

## Output Format

```
DECEPTION ALERT — CRITICAL
━━━━━━━━━━━━━━━━━━━━━━━━━━
Time:         2024-03-15 14:23:07 UTC
Decoy:       FILESERVER-BK04 (10.0.5.200)
Service:      SMB — File share "Finance_Backup" accessed
Source:       192.168.1.105 (WORKSTATION-042, Finance Dept)
User:         company\jsmith
File Accessed: Q4_Revenue_2024.xlsx (decoy document)

Alert Confidence: HIGH — No legitimate reason to access deception asset
False Positive Likelihood: <1%

Automated Response:
  [DONE] WORKSTATION-042 isolated via CrowdStrike
  [DONE] 192.168.1.105 blocked at firewall (bidirectional)
  [DONE] Incident INC0012567 created (P1 — Critical)
  [PENDING] Tier 2 investigation — determine if workstation compromised or insider threat
```
