#!/usr/bin/env bash
# Start the knowledgebase API on the host so it can reach the local RAGFlow.
# Credentials stay in deploy/.env. This script does not print them.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ROOT}/deploy/.env"
REPO_ROOT="$(cd "${ROOT}/../.." && pwd)"
PYTHON="${REPO_ROOT}/.venv/bin/python"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}. Create it before starting." >&2
  exit 1
fi
if [[ ! -x "${PYTHON}" ]]; then
  echo "Missing ${PYTHON}." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

cd "${ROOT}"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
echo "Knowledgebase API listening on http://127.0.0.1:8767"
exec "${PYTHON}" -m flocks_knowledgebase
