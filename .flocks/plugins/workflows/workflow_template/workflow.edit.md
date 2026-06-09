# Workflow Edit Template

## Purpose

Describe the workflow goal, expected operator, and success criteria.

## Inputs

- `input`: Replace with the real input key and expected shape.

## Steps

1. Replace the placeholder workflow node.
2. Describe each node, its input contract, and its output contract.
3. Define error handling and timeout expectations.

## Publish Mode

Choose one publish mode in `config.json` before exposing the workflow:

- API service
- Syslog listener
- Kafka consumer
- Schedule trigger
- Webhook trigger

## Validation

- Run the workflow with representative inputs.
- Confirm `config.json` contains only the publish and trigger sections that should appear on the publish page.
- Confirm no plaintext secrets are stored in this directory.
