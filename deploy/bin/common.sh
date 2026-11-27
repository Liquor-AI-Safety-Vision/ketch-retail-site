# Sourced by the deploy/bin scripts: repo root, deploy/liquor.env, tools.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY="$ROOT/deploy"
if [ ! -f "$DEPLOY/liquor.env" ]; then
  echo "deploy/liquor.env is missing (copy deploy/liquor.env.example)" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
. "$DEPLOY/liquor.env"
set +a
PYTHON="${PYTHON:-python3}"
CADDY="${CADDY:-$(command -v caddy || echo /opt/instance-tools/bin/caddy)}"
MEDIAMTX="${MEDIAMTX:-$(command -v mediamtx || echo /opt/mediamtx/mediamtx)}"
