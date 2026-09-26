# Knowledgebase in Flocks

Core connects directly to independently deployed RAGFlow. The existing BFF routes, file and dataset operations, session selections and `rag_retrieve` contract are unchanged. Core does not run parsers, embedding models or indexes.

Use the existing Flocks configuration and credential management. The merged `api_services.knowledgebase` entry contains:

| Field | Value |
|---|---|
| `enabled` | Defaults to `false` |
| `provider` | `ragflow` is the only implemented provider |
| `base_url` | RAGFlow HTTP(S) origin or deployment/API prefix, without credentials, query or fragment |
| `credential_id` | Plain ID of the RAGFlow API key in `SecretManager` |
| `timeout_seconds` | Optional; defaults to `30.0` |
| `max_upload_bytes` | Optional; defaults to 32 MiB and also bounds engine responses |

Persist ordinary fields through the existing `ConfigWriter.set_api_service` mechanism and the API key through `SecretManager`. Do not place the key, or an automatically expanded `{secret:...}` reference, in this entry. The key must contain at least 32 non-whitespace ASCII characters, as required by the previous service integration. Runtime reads configuration but never writes it.

Configuration is read once at Core startup. Missing, disabled or invalid configuration leaves only the knowledgebase integration disabled. Startup does not contact RAGFlow; `configured`/`ready` still indicate a local client, not remote health. Configuration and credential changes take effect on the next startup, not on each tool call. The old `FLOCKS_KNOWLEDGEBASE_URL` and `FLOCKS_KNOWLEDGEBASE_API_TOKEN` service connection is not reused or reinterpreted.

The independent `flocksrag` service is not implemented. Its adapter is only a rejecting placeholder: selecting it leaves the integration disabled before its URL or credentials are resolved. There is no automatic fallback, engine switching, data migration or second deployment mode to enable in this version.

Flocks lists and uploads files, creates datasets, and stores selected dataset IDs on the current session. `rag_retrieve` reads that selection and retains the existing agent tool permissions. Models and parsing remain in RAGFlow. The old independent knowledgebase service sources are retained as a compatibility baseline, not imported by Core at runtime.
