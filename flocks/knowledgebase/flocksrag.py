"""Reserved adapter; the independent flocksrag service is not implemented."""

from .errors import KnowledgebaseError


class FlocksragAdapter:
    implemented = False

    def __init__(self, *args, **kwargs):
        raise KnowledgebaseError(503, "knowledgebase_not_configured", "flocksrag is not implemented.")
