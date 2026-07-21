# API Reference: Performing Deception Technology Deployment

## Decoy Tokens API (decoytokens.org)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/generate` | POST | Generate a new decoy token (DNS, HTTP, file) |
| `/history` | GET | Retrieve alert history for a token |
| `/manage` | GET | List all deployed tokens |

## Thinkst Decoy API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/decoytokens/create` | POST | Create a new decoytoken |
| `/api/v1/incidents/all` | GET | List all triggered incidents |
| `/api/v1/device/list` | GET | List deployed Decoy devices |

## Honeypot Components (stdlib)

| Module | Description |
|--------|-------------|
| `http.server.HTTPServer` | HTTP honeypot listener |
| `socketserver.TCPServer` | Generic TCP honeypot |
| `secrets.token_hex()` | Generate unique token IDs |
| `hashlib.sha256()` | Hash decoy file content for integrity |

## Key Libraries

- **secrets** (stdlib): Cryptographically secure token generation
- **http.server** (stdlib): HTTP honeypot server implementation
- **socket** (stdlib): TCP/UDP honeypot listeners
- **hashlib** (stdlib): File integrity hashing for decoy files
- **threading** (stdlib): Run honeypot services in background threads

## Honeytoken Types

| Type | Deployment | Alert Trigger |
|------|------------|---------------|
| Credential | AD, LSASS, config files | Any authentication attempt |
| Decoy File | Network shares, endpoints | File open/read access |
| DNS Token | Documents, scripts | DNS resolution |
| AWS Key | Code repos, config files | AWS API call with key |
| HTTP Token | Documents, emails | HTTP GET request |

## Configuration

| Variable | Description |
|----------|-------------|
| `DECOY_API_KEY` | Thinkst Decoy API key |
| `DECOY_DOMAIN` | Decoy DNS domain for token callbacks |
| `HONEYPOT_PORT` | Port for HTTP honeypot listener |

## References

- [Decoytokens.org](https://decoytokens.org/)
- [Thinkst Decoy](https://decoy.tools/)
- [MITRE ATT&CK D3FEND - Decoy](https://d3fend.mitre.org/technique/d3f:Decoy/)
- [OpenDecoy](https://github.com/thinkst/opendecoy)
