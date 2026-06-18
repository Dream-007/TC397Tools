#!/usr/bin/env bash
# Configure + build TasGui. Re-runnable.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j"$(nproc)"
echo "Built: $HERE/build/tasgui"
