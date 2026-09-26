# Knowledgebase boundary

RAGFlow is an independent deployment. The knowledgebase service checks one service token and forwards file, dataset, parse, and retrieval calls. Flocks manages those resources, stores dataset IDs on the current session, and exposes `rag_retrieve`.

Flocks does not register provider credentials into RAGFlow, does not keep a second model history, and does not block startup on the knowledge service.
