#!/usr/bin/env bash
# Failure test under Compose (docs/ARCHITECTURE.md, section 15): steady load with 2 replicas of each
# service; kill one perturbation replica, then one inference replica, while the load runs.
# No run should fail. Then restart the gateway and check the statuses it leaves.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose up -d --scale inference=2 --scale perturbation=2
docker compose run --rm loadgen experiments/loadgen/failure.json &
LOADGEN=$!

sleep 30
victim=$(docker compose ps -q perturbation | head -n 1)
echo "killing perturbation replica $victim"
docker kill "$victim"

sleep 20
victim=$(docker compose ps -q inference | head -n 1)
echo "killing inference replica $victim"
docker kill "$victim"

wait "$LOADGEN" && echo "loadgen: no failed runs" || echo "loadgen: some runs failed (see data/loadtests)"

echo "restarting the gateway while runs are active"
docker compose up -d --scale inference=2 --scale perturbation=2
docker compose run -d --rm vehicle --model wocar_dqn/1 --seed 1 --pace 1 >/dev/null
sleep 5
docker compose restart gateway
sleep 10
echo "runs ended by the restart (expected: GATEWAY_RESTART):"
curl -s "http://127.0.0.1:8000/v1/runs?status=error&limit=5" | python3 -c '
import json, sys
for r in json.load(sys.stdin)["runs"]:
    print(r["runId"], r["status"], r["reason"], r["steps"], "steps")'
