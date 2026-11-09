#!/bin/bash
#OAR -n syntony-example
#OAR -t deploy
#OAR -l nodes=1,walltime=01:00:00
set -euo pipefail
: "${SYNTONY_SOURCE_DIR:?Absolute path to the Syntony source directory}"
: "${SYNTONY_IMAGE:?Reachable versioned Syntony image}"
: "${GRID_ENVIRONMENT:?Environment name available at the selected site}"
cd "$SYNTONY_SOURCE_DIR"
profile=${SYNTONY_PROFILE:-syntony}
[[ "$profile" == syntony || "$profile" == cep ]] || exit 2
work="generated/grid-${OAR_JOB_ID:?}-$profile"
mkdir -p generated
nodes=$(mktemp)
trap 'rm -f "$nodes"' EXIT
sort -u "$OAR_NODEFILE" > "$nodes"
kadeploy3 -f "$nodes" -e "$GRID_ENVIRONMENT" -k
while read -r node; do
  ssh -o StrictHostKeyChecking=accept-new "root@$node" true
done < "$nodes"
# Nodes must already provide Docker Engine + Compose v2, either in the
# selected environment or through your site's documented installation step.
python3 deployment/examples/grid5000/deploy.py prepare --nodes "$nodes" \
  --output "$work" --profile "$profile" --image "$SYNTONY_IMAGE"
# Keep the reservation alive for interactive inspection, then stop services.
cleanup() {
  python3 deployment/examples/grid5000/deploy.py stop --output "$work" || true
  rm -f "$nodes"
}
trap cleanup EXIT TERM INT
python3 deployment/examples/grid5000/deploy.py deploy --output "$work"
python3 deployment/examples/grid5000/deploy.py check --output "$work"
sleep "${SYNTONY_INSPECT_SECONDS:-1200}"
