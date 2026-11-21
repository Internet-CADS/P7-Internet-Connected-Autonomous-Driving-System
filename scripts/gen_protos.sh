#!/usr/bin/env bash
# Regenerate the committed gRPC code from icads/protocol/*.proto.
# CI runs this and fails if anything changes (`git diff --exit-code icads/protocol`).
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python -m grpc_tools.protoc -I . \
  --python_out=. --grpc_python_out=. --pyi_out=. \
  icads/protocol/gateway.proto icads/protocol/perturbation.proto icads/protocol/inference.proto
