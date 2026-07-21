# API Reference: Decoy Tokens for Network Intrusion Detection

## Decoytokens.org Public API

### Create Token

```
POST https://decoytokens.org/generate
Content-Type: application/x-www-form-urlencoded
```

**Parameters:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `type` | Yes | Token type: `dns`, `http`, `aws_keys`, `web_image`, `cloned_web`, `svn`, `sql_server`, `qr_code`, `slack_api`, `doc_msword`, `doc_msexcel`, `pdf_acrobat_reader` |
| `email` | Yes | Notification email address |
| `memo` | Yes | Human-readable label for SOC triage |
| `webhook_url` | No | Webhook URL for real-time POST alerts |

**Example - DNS Token:**
```python
import requests

resp = requests.post("https://decoytokens.org/generate", data={
    "type": "dns",
    "email": "soc@company.com",
    "memo": "Production DB server /etc/app/db.conf",
    "webhook_url": "https://hooks.slack.com/services/T.../B.../xxx",
})
token = resp.json()
# {"hostname": "abc123.decoytokens.com", "url": "https://decoytokens.org/manage?..."}
```

**Example - AWS Key Token:**
```python
resp = requests.post("https://decoytokens.org/generate", data={
    "type": "aws_keys",
    "email": "soc@company.com",
    "memo": "DevOps jump box /home/deploy/.aws/credentials",
})
token = resp.json()
# {"access_key_id": "AKIA...", "secret_access_key": "...", "url": "..."}
```

**Example - HTTP Token:**
```python
resp = requests.post("https://decoytokens.org/generate", data={
    "type": "http",
    "email": "soc@company.com",
    "memo": "Internal wiki emergency passwords page",
})
token = resp.json()
# {"url": "http://decoytokens.com/..."}
```

## Thinkst Decoy Enterprise API

### Authentication

All enterprise API calls require `auth_token` parameter.

```
Base URL: https://{console_domain}.decoy.tools/api/v1/
```

### Create Token

```
POST /api/v1/decoytoken/create
```

**Parameters:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `auth_token` | Yes | API authentication token |
| `memo` | Yes | Description for the token |
| `kind` | Yes | Token kind (see below) |
| `flock_id` | No | Flock ID for grouping |

**Supported Kinds:** `dns`, `http`, `aws-id`, `doc-msword`, `doc-msexcel`, `slack-api`, `svn`, `cloned-css`, `cloned-web`, `qr-code`, `sql-server`

```python
import requests

url = "https://yourcompany.decoy.tools/api/v1/decoytoken/create"
resp = requests.post(url, data={
    "auth_token": "YOUR_AUTH_TOKEN",
    "memo": "Production honeytoken",
    "kind": "dns",
})
```

### List Tokens

```
GET /api/v1/decoytokens/fetch?auth_token=YOUR_AUTH_TOKEN
```

### Get Triggered Alerts

```
GET /api/v1/decoytokens/alerts?auth_token=YOUR_AUTH_TOKEN
```

### Using Python Client Library

```python
import decoytools

console = decoytools.Console(domain="yourcompany", api_key="YOUR_API_KEY")

# Create tokens
dns_token = console.tokens.create(memo="DNS beacon", kind=decoytools.DecoyTokenKinds.DNS)
aws_token = console.tokens.create(memo="AWS keys", kind=decoytools.DecoyTokenKinds.AWS_ID)

# List all tokens
tokens = console.tokens.all()

# Get alerts
alerts = console.tokens.alerts()
```

## Webhook Alert Payload Format

When a decoy token is triggered, the webhook receives a POST with this payload:

```json
{
    "manage_url": "https://decoytokens.org/manage?token=abc123&auth=xyz",
    "memo": "Production DB server /etc/app/db.conf",
    "additional_data": {
        "src_ip": "203.0.113.50",
        "useragent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "referer": "",
        "location": ""
    },
    "channel": "DNS",
    "time": "2026-01-15 14:23:00 (UTC)",
    "src_ip": "203.0.113.50"
}
```

**Fields:**

| Field | Description |
|-------|-------------|
| `manage_url` | URL to manage/disable the token |
| `memo` | The description set during creation |
| `channel` | Token type that triggered (DNS, HTTP, AWS) |
| `src_ip` | Source IP of the triggering request |
| `time` | UTC timestamp of the trigger event |
| `additional_data` | Extra context (User-Agent, referer, etc.) |

## Token Placement Matrix

| Token Type | Recommended Location | Trigger Action |
|------------|---------------------|----------------|
| DNS | Config files, `/etc/hosts`, SSH config | DNS resolution |
| HTTP | Internal wikis, HTML pages, bookmarks | HTTP GET request |
| AWS Keys | `~/.aws/credentials`, `.env` files, repos | AWS API call |
| Web Image | HTML pages, email signatures | Image HTTP load |
| Cloned Web | Internal admin portals | Page visit |
| SVN | Repository configs | SVN checkout |
| SQL Server | Connection strings, config files | DB login attempt |
| Slack API | Source code, CI/CD configs | Slack API call |
| QR Code | Physical locations, printed docs | QR scan + URL visit |

## MITRE ATT&CK Mapping

| Technique | ID | Decoy Token Detection |
|-----------|----|----------------------|
| Account Discovery | T1087 | AWS key tokens detect credential testing |
| File and Directory Discovery | T1083 | Document/config tokens detect file access |
| Network Service Discovery | T1046 | DNS tokens detect network scanning |
| Valid Accounts: Cloud | T1078.004 | AWS key tokens detect credential abuse |
| Unsecured Credentials: Files | T1552.001 | Credential file tokens detect harvesting |
| Data from Network Shared Drive | T1039 | Document tokens detect share browsing |

## References

- Decoytokens Documentation: https://docs.decoytokens.org/guide/
- Decoytokens DNS Tokens: https://docs.decoytokens.org/guide/dns-token.html
- Decoytokens HTTP Tokens: https://docs.decoytokens.org/guide/http-token.html
- Decoytokens AWS Key Tokens: https://docs.decoytokens.org/guide/aws-keys-token.html
- Thinkst Decoy API Docs: https://docs.decoy.tools/decoytokens/actions.html
- Thinkst Python Client: https://github.com/thinkst/decoytools-python
- Decoytokens Open Source: https://github.com/thinkst/decoytokens
- Zeltser Honeytoken Guide: https://zeltser.com/honeytokens-decoytokens-setup/
