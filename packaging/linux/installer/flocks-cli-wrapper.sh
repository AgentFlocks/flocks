#!/usr/bin/env bash
# Run the packaged Flocks CLI with the service environment (installed as /usr/local/bin/flocks).
# As root it switches to the service user so `sudo flocks status|logs|stop` talk to the running service.
set -euo pipefail
ENV_FILE="@ENV_FILE@"
FLOCKS_CLI="@FLOCKS_CLI@"
SERVICE_USER="@SERVICE_USER@"
if [[ "$(id -u)" -eq 0 && "$SERVICE_USER" != "root" ]]; then
  # the service user cannot necessarily read root's current directory, so start from the repo dir
  exec runuser -u "$SERVICE_USER" -- bash -c 'set -a; . "$0" || exit 97; set +a; cd "${FLOCKS_REPO_ROOT:-/}" 2>/dev/null || cd /; exec "$@"' "$ENV_FILE" "$FLOCKS_CLI" "$@"
fi
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
exec "$FLOCKS_CLI" "$@"
