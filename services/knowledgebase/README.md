# Knowledgebase service

RAGFlow runs as its own deployment. This service is the API Flocks calls. It checks one service token and forwards file, dataset, parse and retrieval requests. It does not store files, datasets, users or model credentials.

## Configuration

- `KB_API_TOKEN`: token Flocks sends as `Authorization: Bearer`.
- `KB_RAGFLOW_BASE_URL`: independent RAGFlow origin.
- `KB_RAGFLOW_API_KEY`: RAGFlow API token. It stays in this service.

`deploy/init_env.py` creates a new `KB_API_TOKEN` and refuses to overwrite an existing env file. Set the RAGFlow URL and token yourself. `deploy/compose.yaml` publishes the API on `127.0.0.1:18767` only.
