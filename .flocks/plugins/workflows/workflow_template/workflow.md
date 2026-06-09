# Hidden Workflow Publish Template

This directory is an internal template for project workflows. It is intentionally hidden from frontend workflow lists and initial agent system prompts by `meta.json` and `workflow.json.metadata`.

## Files

- `workflow.json`: executable workflow definition. Replace the placeholder node when copying the template.
- `workflow.md`: read-only generated documentation shown for real workflows.
- `workflow.edit.md`: editable workflow document used by the workflow editing page.
- `config.json`: publish-page template. The visible publish page is derived from the enabled sections in this file.
- `meta.json`: workflow metadata and visibility flags.

## Visibility Contract

Keep these flags on template-only workflow directories:

- `hidden: true`
- `templateOnly: true`
- `visibility: hidden`
- `excludeFromUI: true`
- `excludeFromPrompt: true`

Remove those flags only after copying the template into a real workflow directory.

## Publish Config Contract

The publish page should render from `config.json`.

- If `publish.type` is `api_service`, show the API publish controls.
- If the only configured trigger is `syslog`, show only syslog listener start/stop controls. Syslog host, port, framing, parser, filters, and input mapping already live in `config.json`.
- If the only configured trigger is `kafka`, show only kafka consumer start/stop controls. Kafka connection and topic details already live in `config.json`.
- If the only configured trigger is `schedule`, show only schedule start/stop controls. Cron/interval details already live in `config.json`.
- Do not write plaintext secrets into `config.json`; store only booleans such as `apiKeyConfigured` or secret references.
