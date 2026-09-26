# Knowledgebase in Flocks

RAGFlow is deployed on its own. This package calls the knowledgebase service with `FLOCKS_KNOWLEDGEBASE_URL` and `FLOCKS_KNOWLEDGEBASE_API_TOKEN`. If those are absent, the integration stays disabled and does not contact the network during Core startup.

Flocks lists and uploads files, creates datasets, and stores the selected dataset IDs on the current session. Embedding models stay in RAGFlow. `rag_retrieve` reads that session selection. It is available when an agent's tool list includes it; agents without an explicit tool list do not receive it automatically.
