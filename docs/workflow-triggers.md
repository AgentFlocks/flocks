# Workflow Triggers

## Overview

Flocks workflows now support a unified `triggers` definition in `workflow.json`.
This brings webhook, schedule, syslog, kafka, and custom trigger sources under a
single runtime model while keeping the legacy per-feature endpoints compatible.

At a high level the runtime now has four layers:

1. `TriggerDefinition`: persisted workflow trigger schema.
2. `TriggerEvent`: normalized event envelope for all trigger sources.
3. `EventDispatcher`: filter + mapping + `_flocks.trigger` envelope injection.
4. `TriggerRuntime`: lifecycle management for legacy adapters and custom adapters.

## Workflow JSON

Triggers are stored in the root `workflowJson.triggers` field:

```json
{
  "start": "n1",
  "nodes": [
    {
      "id": "n1",
      "type": "python",
      "code": "result = {'ok': True}"
    }
  ],
  "edges": [],
  "triggers": [
    {
      "id": "hook-default",
      "type": "custom_webhook",
      "enabled": true,
      "source": {
        "path": "/alerts/demo",
        "method": "POST"
      },
      "mapping": {
        "event": "$.body"
      }
    }
  ]
}
```

For backward compatibility, older workflows that still keep trigger
configuration under `metadata.triggers` are also supported on read.

## Event Envelope

All trigger executions inject a reserved `_flocks.trigger` envelope into the
workflow inputs:

```json
{
  "_flocks": {
    "trigger": {
      "id": "hook-default",
      "type": "custom_webhook",
      "source": "/alerts/demo",
      "deliveryId": "webhook-123",
      "receivedAt": 1760000000000,
      "attempt": 1
    }
  }
}
```

Legacy poller/syslog/kafka runs now also record `triggerId`, `triggerType`,
`deliveryId`, and `triggerSource` in workflow execution history.

## API

Unified trigger routes:

- `GET /api/workflow/{id}/triggers`
- `POST /api/workflow/{id}/triggers`
- `PUT /api/workflow/{id}/triggers/{trigger_id}`
- `POST /api/workflow/{id}/triggers/{trigger_id}/preview-mapping`
- `POST /api/workflow/{id}/triggers/{trigger_id}/test`
- `GET /api/workflow/{id}/triggers/{trigger_id}/status`
- `GET /api/workflow-trigger-plugins`
- `POST /webhook/workflows/{workflow_id}/{trigger_id}`

The legacy routes still work:

- `POST /api/workflow/{id}/syslog-config`
- `POST /api/workflow/{id}/kafka-config`
- `POST /api/workflow/{id}/poller-config`

Saving through a legacy route now also updates the unified `workflowJson.triggers`
representation so the new Automation / Triggers view stays in sync.

## Legacy Compatibility

The trigger runtime wraps the existing managers:

- `schedule` -> `WorkflowPollerManager`
- `syslog` -> `SyslogManager`
- `kafka` -> `KafkaManager`

On startup the runtime syncs unified trigger definitions back into the legacy
storage keys used by those managers, then starts them once.

## Custom Triggers

Two custom paths are supported:

1. `custom_webhook`: declarative webhook trigger persisted in workflow JSON.
2. `custom_adapter`: plugin-backed trigger loaded from:
   - `~/.flocks/plugins/triggers/<id>/`
   - `<workspace>/.flocks/plugins/triggers/<id>/`

Supported manifest filenames:

- `trigger.json`
- `trigger.yaml`
- `trigger.yml`
- `manifest.json`

Each plugin directory can expose either:

- `create_trigger_adapter(trigger_definition)`
- `TriggerAdapter(trigger_definition)`

The runtime calls `adapter.start(definition, emit)` and expects the adapter to
send normalized events back through the provided `emit()` callback.

## Frontend

The workflow detail page now includes an `Automation / Triggers` section that:

- lists all unified triggers
- shows runtime status
- supports quick webhook creation
- previews mapped workflow inputs
- runs test trigger events

The workflow canvas also renders configured triggers as virtual upstream nodes
connected to the workflow start node.

