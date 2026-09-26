import pytest

from flocks.knowledgebase.errors import KnowledgebaseError
from flocks.knowledgebase.retrieval import retrieve_for_session
from flocks.knowledgebase.session_datasets import detach_dataset, get_selection, set_selection
from flocks.session.policy import SessionPolicy
from flocks.session.session import Session


class _Session:
    def __init__(self):
        self.id = "session-1"
        self.project_id = "project-1"
        self.metadata = {}
        self.status = "active"


@pytest.fixture
def session(monkeypatch):
    current = _Session()

    async def get_by_id(session_id):
        return current if session_id == current.id else None

    async def mutate(project_id, session_id, mutator):
        current.metadata = mutator(dict(current.metadata))
        return current

    async def list_all_unfiltered():
        return [current]

    monkeypatch.setattr(Session, "get_by_id", staticmethod(get_by_id))
    monkeypatch.setattr(Session, "mutate_metadata", staticmethod(mutate))
    monkeypatch.setattr(Session, "list_all_unfiltered", staticmethod(list_all_unfiltered))
    monkeypatch.setattr(SessionPolicy, "can_read", classmethod(lambda cls, item, user=None, **kwargs: user != "stranger"))
    monkeypatch.setattr(SessionPolicy, "can_write", classmethod(lambda cls, item, user=None: user == "owner"))
    return current


@pytest.mark.asyncio
async def test_owner_can_save_dataset_ids_including_an_empty_selection(session):
    saved = await set_selection(session.id, "owner", ["dataset-a"])
    assert saved["dataset_ids"] == ["dataset-a"]
    assert session.metadata["knowledgebase"]["dataset_ids"] == ["dataset-a"]
    cleared = await set_selection(session.id, "owner", [])
    assert cleared["dataset_ids"] == []
    visible = await get_selection(session.id, "reader")
    assert visible["dataset_ids"] == []


@pytest.mark.asyncio
async def test_deleting_a_dataset_removes_it_from_the_session(session):
    await set_selection(session.id, "owner", ["dataset-a", "dataset-b"])
    await detach_dataset("dataset-a")
    assert session.metadata["knowledgebase"]["dataset_ids"] == ["dataset-b"]


@pytest.mark.asyncio
async def test_other_user_cannot_change_the_session(session):
    with pytest.raises(KnowledgebaseError) as caught:
        await set_selection(session.id, "stranger", [])
    assert caught.value.status == 403


@pytest.mark.asyncio
async def test_retrieval_uses_the_session_subset_and_an_empty_scope_skips_the_service(monkeypatch):
    calls = []

    async def selection(session_id, user):
        return {"session_id": session_id, "dataset_ids": ["alpha", "beta"]}

    class Client:
        async def retrieve(self, payload):
            calls.append(payload)
            return {"chunks": [{"content": "hit"}], "total": 1}

    monkeypatch.setattr("flocks.knowledgebase.retrieval.get_selection", selection)
    result = await retrieve_for_session("session-1", "owner", "question", client=Client(), dataset=["alpha"])
    assert result["total"] == 1
    assert calls[0]["dataset_ids"] == ["alpha"]
    await retrieve_for_session("session-1", "owner", "question", client=Client(), dataset=[])
    assert calls[1]["dataset_ids"] == ["alpha", "beta"]
    with pytest.raises(KnowledgebaseError) as caught:
        await retrieve_for_session("session-1", "owner", "question", client=Client(), dataset=["other"])
    assert caught.value.status == 403

    async def empty(session_id, user):
        return {"session_id": session_id, "dataset_ids": []}

    monkeypatch.setattr("flocks.knowledgebase.retrieval.get_selection", empty)
    skipped = await retrieve_for_session("session-1", "owner", "question", client=Client())
    assert skipped == {"chunks": [], "total": 0}
    assert len(calls) == 2
