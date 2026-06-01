#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "${repo_root}/build"

g++ -std=c++17 -O3 -fPIC -shared \
  -I"${repo_root}/ELFIO" \
  "${repo_root}/cpp/tc397_elfio_resolver.cpp" \
  -o "${repo_root}/build/libtc397_elfio_resolver.so"

cp "${repo_root}/build/libtc397_elfio_resolver.so" \
  "${repo_root}/scripts/libtc397_elfio_resolver.so"

echo "${repo_root}/build/libtc397_elfio_resolver.so"
