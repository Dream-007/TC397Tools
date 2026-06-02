#!/usr/bin/env bash
# Offline smoke test for TC397Tools.
#
# Exercises everything that does not require a physical TC397 board:
#   - venv has pyelftools (isystem.connect is no longer used)
#   - libdas_api.so + tas_server present
#   - C++ ELFIO resolver shared library (builds it on the fly if missing)
#   - scripts/base_tas.py: JSON index generation, VariableIndex load, leaf lookup
#
# It does NOT call BaseTas.connect() / read_* / write_*, because those need
# the JDS probe + a powered TC397 board AND will perform live target reads
# (and `python scripts/base_tas.py` even *writes* — see Gotchas).
#
# With the board attached, exercise the target path manually:
#   python scripts/base_tas.py       # CAUTION: writes VehModMngtGlbSafe1UsgModSts
#
# Run from anywhere; paths are anchored to the repo root.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYTHON="${PYTHON:-$HOME/.virtualenvs/test_env/bin/python}"
ELF="${ELF:-$REPO_ROOT/Downloads/MCU_A.elf}"
JSON="${JSON:-$REPO_ROOT/build/MCU_A.json}"
MEMBER="${MEMBER:-VehModMngtGlbSafe1UsgModSts}"
DAS_HOME="${DAS_HOME:-/opt/Tools/DAS/8.3.0}"

step() { printf '\n=== %s ===\n' "$*"; }
fail() { printf '\n[FAIL] %s\n' "$*" >&2; exit 1; }

step "Python interpreter"
[ -x "$PYTHON" ] || fail "venv python not found at $PYTHON (workon test_env or set PYTHON=...)"
"$PYTHON" --version
"$PYTHON" -c "import elftools; print('pyelftools OK')"

step "ELF sample"
[ -f "$ELF" ] || fail "ELF not found: $ELF (set ELF=/path/to/app.elf, or rely on a usable cached JSON)"
ls -lh "$ELF"

step "DAS toolchain"
[ -f "$DAS_HOME/lib/libdas_api.so" ] || fail "missing $DAS_HOME/lib/libdas_api.so — install DASTool/DAS_8.3.0_linux_x64.deb"
[ -x "$DAS_HOME/bin/tas_server" ]    || fail "missing $DAS_HOME/bin/tas_server"
ls -1 "$DAS_HOME/lib/libdas_api.so" "$DAS_HOME/bin/tas_server"

step "C++ ELFIO resolver shared library"
if [ ! -f "$REPO_ROOT/scripts/libtc397_elfio_resolver.so" ]; then
  if command -v g++ >/dev/null; then
    "$REPO_ROOT/scripts/build_tc397_elfio_resolver.sh"
  else
    fail "scripts/libtc397_elfio_resolver.so missing and g++ not installed"
  fi
fi
ls -lh "$REPO_ROOT/scripts/libtc397_elfio_resolver.so"

step "base_tas.py: ensure_json_index (regenerates if md5 mismatched; ~40s cold on MCU_A.elf, <1s warm)"
ELF="$ELF" JSON="$JSON" MEMBER="$MEMBER" "$PYTHON" - <<'PY'
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(os.environ["PWD"]) / "scripts") if False else "scripts")
from base_tas import BaseTas, ensure_json_index, json_matches_elf, json_is_usable

elf  = Path(os.environ["ELF"])
jpath = Path(os.environ["JSON"])
member = os.environ["MEMBER"]

t0 = time.monotonic()
ensure_json_index(elf, jpath)
print(f"ensure_json_index: {time.monotonic()-t0:.2f}s "
      f"usable={json_is_usable(jpath)} matches_elf={json_matches_elf(elf, jpath)}")

with jpath.open() as f:
    data = json.load(f)
print(f"json keys     = {sorted(data.keys())}")
print(f"member_count  = {len(data['entries_by_member'])}")
print(f"elf_md5       = {data['elf_md5']}")

step = lambda *a: print("---", *a, "---")
step("base_tas.BaseTas.resolve_variable (leaf name -> full DWARF path)")
tas = BaseTas(elf_path=elf, json_path=jpath)
info = tas.resolve_variable(member)
print(f"name    = {info.name}")
print(f"address = 0x{info.address:08x}")
print(f"size    = {info.byte_size}")
print(f"type    = {info.type_name}")
print(f"base    = {info.base_name}")
print(f"offset  = {info.byte_offset}")
PY

printf '\nAll offline checks passed.\n'
printf 'Target path (BaseTas.connect / read_variable / write_variable) needs the JDS probe + powered board.\n'
printf 'WARNING: `python scripts/base_tas.py` will WRITE %s on the live target.\n' "$MEMBER"
