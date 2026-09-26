import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from flocks_knowledgebase.app import create_app
from flocks_knowledgebase.settings import Settings

TOKEN = "s" * 40
RAG_KEY = "r" * 40
FILE_ID = "a" * 32
DATASET_ID = "b" * 32


class Engine:
    def __init__(self):
        self.files = {}
        self.datasets = {}
        self.documents = {}
        self.parsed = []

    async def list_files(self, parent_id=None, *, page=1, page_size=100, keywords=None):
        items = list(self.files.values())
        return {"files": items, "total": len(items), "parent_folder": None}

    async def upload_file(self, filename, content, parent_id=None, content_type="application/octet-stream"):
        self.files[FILE_ID] = {"id": FILE_ID, "name": filename, "type": "doc", "size": len(content), "parent_id": None}
        return self.files[FILE_ID]

    async def download_file(self, file_id):
        return b"hello", "text/plain"

    async def delete_files(self, ids):
        for ident in ids:
            self.files.pop(ident, None)
        return {}

    async def list_datasets(self, *, page=1, page_size=100):
        items = list(self.datasets.values())
        return {"datasets": items, "total": len(items)}

    async def get_dataset(self, ident):
        return self.datasets[ident]

    async def create_dataset(self, payload):
        self.datasets[DATASET_ID] = {"id": DATASET_ID, **payload, "document_count": 0, "chunk_count": 0}
        return self.datasets[DATASET_ID]

    async def delete_dataset(self, ident):
        self.datasets.pop(ident, None)

    async def link_files(self, file_ids, dataset_ids):
        self.documents["doc1"] = {"id": "doc1", "name": "note.txt", "run": "UNSTART", "progress": 0, "chunk_count": 0}

    async def list_documents(self, dataset_id, *, page=1, page_size=100):
        return {"docs": list(self.documents.values()), "total": len(self.documents)}

    async def parse_documents(self, dataset_id, doc_ids):
        self.parsed.extend(doc_ids)
        return {}

    async def remove_documents(self, dataset_id, doc_ids):
        for ident in doc_ids:
            self.documents.pop(ident, None)
        return {}

    async def retrieve(self, payload):
        return {
            "chunks": [
                {
                    "id": "c1",
                    "content": "kept",
                    "dataset_id": payload["dataset_ids"][0],
                    "document_id": "doc1",
                    "similarity": 0.9,
                },
                {"id": "c2", "content": "dropped", "dataset_id": "other-dataset", "document_id": "x", "similarity": 0.1},
            ],
            "total": 2,
        }

    async def close(self):
        return None


def _app(engine):
    settings = Settings(
        api_token=SecretStr(TOKEN),
        ragflow_base_url="http://ragflow.test",
        ragflow_api_key=SecretStr(RAG_KEY),
    )
    return create_app(settings, ragflow=engine)


@pytest.fixture
async def api():
    engine = Engine()
    app = _app(engine)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://kb.test") as client:
            yield client, engine


async def test_missing_token_is_rejected_without_leaking_secrets(api):
    client, _engine = api
    response = await client.get("/v1/files")
    assert response.status_code == 401
    body = response.text
    assert TOKEN not in body
    assert RAG_KEY not in body


async def test_file_dataset_parse_and_retrieval_round_trip(api):
    client, engine = api
    headers = {"Authorization": f"Bearer {TOKEN}"}
    uploaded = await client.post("/v1/files", headers=headers, files={"file": ("note.txt", b"hello", "text/plain")})
    assert uploaded.status_code == 201
    assert uploaded.json()["data"]["id"] == FILE_ID

    listed = await client.get("/v1/files", headers=headers)
    assert listed.json()["data"]["items"][0]["name"] == "note.txt"

    created = await client.post(
        "/v1/datasets",
        headers=headers,
        json={"name": "Notes", "description": ""},
    )
    assert created.status_code == 201
    dataset_id = created.json()["data"]["id"]
    assert "embedding_model" not in created.json()["data"]

    linked = await client.post(f"/v1/datasets/{dataset_id}/files", headers=headers, json={"file_ids": [FILE_ID]})
    assert linked.status_code == 200
    parsed = await client.post(
        f"/v1/datasets/{dataset_id}/parse", headers=headers, json={"document_ids": ["doc1"]}
    )
    assert engine.parsed == ["doc1"]
    found = await client.post(
        "/v1/retrieval",
        headers=headers,
        json={"dataset_ids": [dataset_id], "keywords": "hello"},
    )
    chunks = found.json()["data"]["chunks"]
    assert [chunk["content"] for chunk in chunks] == ["kept"]
    assert RAG_KEY not in found.text
