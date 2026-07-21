# API Reference — Implementing Deception-Based Detection with Decoy Token

## Libraries Used
- **requests**: HTTP client for Thinkst Decoy Console REST API
- **json**: JSON serialization for audit reports

## CLI Interface
```
python agent.py --console abc123 --auth-token TOKEN ping
python agent.py --console abc123 --auth-token TOKEN list
python agent.py --console abc123 --auth-token TOKEN alerts
python agent.py --console abc123 --auth-token TOKEN create --kind http --memo "Web server token"
python agent.py --console abc123 --auth-token TOKEN create --kind dns --memo "DNS honeypot"
python agent.py --console abc123 --auth-token TOKEN coverage
python agent.py --console abc123 --auth-token TOKEN full
```

## Core Functions

### `DecoyClient(console_domain, auth_token)` — API client
Base URL: `https://{console_domain}.decoy.tools/api/v1`
Auth: `auth_token` parameter on every request.

### `create_token(kind, memo, **kwargs)` — Create Decoytoken
POST `/decoytoken/create` with `kind`, `memo`, `auth_token`.
For doc-msword: uploads file via multipart form with MIME type
`application/vnd.openxmlformats-officedocument.wordprocessingml.document`.

### `list_tokens()` — List all deployed tokens
GET `/decoytokens/fetch`. Returns array of token objects with kind, memo, url, enabled.

### `get_alerts(newer_than)` — Fetch triggered token alerts
GET `/incidents/all`. Optional `newer_than` timestamp filter.
Returns src_host (source IP), description, timestamp, acknowledged status.

### `ack_alert(incident_id)` — Acknowledge an alert
POST `/incident/acknowledge` with incident ID.

### `audit_token_coverage(client)` — Coverage analysis
Calculates: tokens by kind, triggered vs untriggered, missing token types,
coverage score as percentage of TOKEN_KINDS deployed.

### `full_audit(client)` — Comprehensive deception audit

## Decoy Console API Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/ping` | GET | Test API connectivity |
| `/decoytoken/create` | POST | Create new token |
| `/decoytokens/fetch` | GET | List all tokens |
| `/decoytoken/fetch` | GET | Get specific token |
| `/decoytoken/delete` | POST | Delete a token |
| `/incidents/all` | GET | Fetch all alerts |
| `/decoytoken/incidents` | GET | Alerts for specific token |
| `/incident/acknowledge` | POST | Acknowledge alert |

## Supported Token Types
| Kind | Description |
|------|-------------|
| http | Web bug — triggers on HTTP request |
| dns | DNS token — triggers on DNS resolution |
| doc-msword | MS Word document with embedded beacon |
| pdf-acrobat-reader | PDF with embedded beacon |
| aws-id | Fake AWS API key pair |
| web-image | Image with tracking pixel |
| cloned-web | Cloned website detection |
| qr-code | QR code with tracking URL |
| sensitive-cmd | Triggers on command execution |
| windows-dir | Windows folder open detection |

## Dependencies
- `requests` >= 2.28.0
- Thinkst Decoy Console account with API auth token
