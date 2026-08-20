"""Cleanup helpers for n8n test workflows."""

from __future__ import annotations

from typing import Any, Dict, List

from flocks.integrations.n8n.client import N8nClient, N8nClientError


def cleanup_workflows(client: N8nClient, workflow_ids: List[str]) -> List[Dict[str, Any]]:
    results = []
    for workflow_id in workflow_ids:
        try:
            response = client.delete_workflow(workflow_id)
            results.append({"workflow_id": workflow_id, "success": True, "status": response.get("status")})
        except N8nClientError as exc:
            results.append({"workflow_id": workflow_id, "success": False, "error": str(exc), "status": exc.status})
    return results

