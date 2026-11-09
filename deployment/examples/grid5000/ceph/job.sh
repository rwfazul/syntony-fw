#!/bin/bash
#OAR -n ceph-syntony-example
#OAR -t deploy
#OAR -l nodes=6,walltime=02:00:00
set -euo pipefail
: "${SYNTONY_SOURCE_DIR:?Syntony source directory on the frontend}"
: "${GRID_ENVIRONMENT:?Kadeploy environment with Docker Engine and Compose v2}"
: "${GRID_SITE:?Reservation site}"
: "${GRID_CLUSTER:?Reservation cluster}"
cd "$SYNTONY_SOURCE_DIR"
work="generated/ceph-${OAR_JOB_ID:?}"
mkdir -p generated
nodes=$(mktemp)
trap 'rm -f "$nodes"' EXIT
sort -u "$OAR_NODEFILE" > "$nodes"
kadeploy3 -f "$nodes" -e "$GRID_ENVIRONMENT" -k
while read -r node; do
  ssh -o StrictHostKeyChecking=accept-new "root@$node" docker compose version
done < "$nodes"
script=deployment/examples/grid5000/ceph/cluster.py
python3 "$script" prepare --nodes "$nodes" --site "$GRID_SITE" --cluster "$GRID_CLUSTER" \
  --layout "${SYNTONY_LAYOUT:-collocated}" --output "$work"
# Validate remote cluster ownership before registering cleanup.
python3 "$script" sync --output "$work"
# Register cleanup before image/start operations so partial startup is handled.
cleanup() {
  python3 "$script" stop --output "$work" || true
  rm -f "$nodes"
}
trap cleanup EXIT TERM INT
python3 "$script" images --output "$work"
python3 "$script" start --output "$work"
python3 "$script" start-syntony --output "$work"
python3 "$script" check --output "$work"
sleep "${SYNTONY_INSPECT_SECONDS:-1200}"
