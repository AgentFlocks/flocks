# Flocks Knowledge Base Guide

English · [简体中文](README_cn.md)

The Flocks knowledge base integrates RAGFlow directly. This guide covers installing Flocks and RAGFlow, connecting them, and using Knowledge Sets from chat and Workflows. **Knowledge Sets** is the Flocks term for Datasets in RAGFlow.

## 1. Start RAGFlow and configure an embedding model

Follow the [RAGFlow deployment guide](https://ragflow.io/docs) to install Docker and RAGFlow. Start it using the Compose file supplied by RAGFlow:

```bash
git clone https://github.com/infiniflow/ragflow.git
cd ragflow/docker
docker compose -f docker-compose.yml up -d
```

Sign in to RAGFlow at the address shown by your deployment (commonly `http://127.0.0.1:9380`). If you changed the published port, use that port instead.

1. Open **User settings → Model providers**. Add your embedding provider, its provider API key and service URL, then register the exact model as type **Embedding**.
2. Check that the model works, and select it as the default **Embedding model** in **System Model Settings**.
3. After creating a Knowledge Set, check its actual embedding model in RAGFlow. We recommend using the same embedding model for sets you plan to search together.
4. In RAGFlow, open your avatar menu → **API**, and generate a **RAGFlow API Key**. This is the credential Flocks uses to call RAGFlow; it is **not** the embedding provider's key.

If the embedding service runs elsewhere, make sure the **RAGFlow container** can reach it. `127.0.0.1` inside a container does not refer to the Docker host. UI labels and file APIs can differ between RAGFlow releases; consult its [official documentation](https://ragflow.io/docs) for your deployment.

## 2. Install and start Flocks

Use the [Flocks installer](../../README.md); on macOS/Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/AgentFlocks/flocks/main/install.sh | bash
```

Follow the installation prompts, enter the installed directory, and open a **new terminal** to run:

```bash
flocks start
```

The installer and startup command handle the Flocks build and startup. Open the WebUI address printed on startup. On first use, follow the page instructions to create a local administrator account and sign in.

## 3. Connect Flocks to RAGFlow

Open **Workspace → Knowledge Base → Connection settings** (the unconfigured state also has this entry). The right-hand drawer contains:

- **Knowledge engine:** RAGFlow is currently the only option.
- **RAGFlow URL:** An address reachable from the **Flocks backend**. On the same machine, this may be `http://127.0.0.1:9380`. If Flocks runs in a container or on another host, use an address it can actually reach.
- **API Key:** The key from RAGFlow's **API** page. When a key is already saved, the field shows a masked **configured** placeholder, not the actual key. Leave it unchanged to keep the saved key, or enter a replacement. Changing the URL (including its path) requires entering a key again.

Click **Save**. Flocks first performs two authenticated, **read-only checks**: listing Knowledge Sets and listing files. Successful saves **take effect immediately** and refresh the connection status and lists. If validation or saving fails, the existing connection remains active. The key is stored in Flocks' credential store; its actual value is not sent back to the browser.

Requests already using the previous connection can finish before it is closed. A successful save checks those two list APIs, not the full upload-and-retrieval flow below.

## 4. Upload a file and build a Knowledge Set

1. **Upload:** In **Workspace → Knowledge Base → Knowledge Files**, click **Upload file**. This stores the original file; it does **not** start parsing. The default file limit is 32 MB, and multipart overhead can also cause uploads near that limit to fail.
2. **Create:** Switch to **Knowledge Sets** and click **Create Knowledge Set**. Give it a name and description. The form does not choose an embedding model; configure RAGFlow's default first.
3. **Link:** Open your new Knowledge Set and click **Link existing files** to associate the uploaded file. Wait until a document appears in the linked-file list. File IDs and linked-document IDs are different.
4. **Parse:** Click **Start parsing** on that document. RAGFlow will parse and index it. The current page does **not** poll for completion automatically; reopen the set's detail view to check document status before retrieving its content.

## 5. Use a Knowledge Set in chat

In the **same session** you plan to ask from, open **Session → Context → Knowledge Sets**, select the sets, then click **Apply Knowledge Sets**. An empty selection does not search all sets.

Ask an Agent that has permission to use `rag_retrieve` (for example, Rex with this built-in tool enabled):

> Consult the Knowledge Sets first, then answer based on the passages you find and name any document you can verify. If there are no matches, say so.

Tool execution depends on Agent tools, permissions and the model's tool choice. Retrieval returns passages; it does not enforce citation formatting or automatically generate an answer. If a multi-set query fails, check that the sets use compatible embedding models.

## 6. Use a Knowledge Set in a Workflow

A Workflow can invoke `rag_retrieve` in a **tool node** with `dataset` (Knowledge Sets), `keywords` (the question) and optionally `top_k`, and pass the result to a later answer node. Prefer starting the Workflow **from the same session** with its Knowledge Sets selected, using an Agent allowed to call both `run_workflow` and `rag_retrieve`.

> There is no dedicated “Select Knowledge Set” Workflow node. Include the `dataset` parameter when calling the `rag_retrieve` tool.

## Troubleshooting

| Symptom | What to check |
|---|---|
| “Knowledge Base is not configured” | Open Connection settings and save a valid connection. A successful save applies immediately; if the page is stale, click Check connection again. |
| Saving reports a connection-check failure | Check reachability from the Flocks backend, the RAGFlow API Key and tenant permissions, and RAGFlow's dataset and file-list APIs. An anonymous health response does not validate the key. |
| An uploaded file cannot be found in chat | Link it to a set, wait for the document to appear, start parsing, confirm indexing completes, select the set in the **current session**, and check that the Agent may use `rag_retrieve`. |
| An upload is rejected | Check its file name and size; multipart request overhead also matters close to the limit. |

For more installation and deployment details, consult the [Flocks installation guide](../../README.md) and [RAGFlow documentation](https://ragflow.io/docs).
