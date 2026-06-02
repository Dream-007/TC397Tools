---
name: run-tc397tools
description: Run, smoke-test, or drive the TC397Tools toolkit on Linux — build the C++ ELF/DWARF resolver, generate or refresh the MCU_A.json member index, resolve leaf member names to addresses, and read/write TC397 memory through Infineon DAS/TAS. Use when asked to run TC397Tools, exercise scripts/base_tas.py, build libtc397_elfio_resolver.so, regenerate MCU_A.json, look up a DWARF member, or sanity-check changes to scripts/base_tas.py or cpp/tc397_elfio_resolver.cpp.
---

TC397Tools is a Python toolkit with one C++ component. The flow is fixed: build `scripts/libtc397_elfio_resolver.so` from `cpp/tc397_elfio_resolver.cpp` (against the vendored `ELFIO/` headers), call it from Python to turn `Downloads/MCU_A.elf` into `build/MCU_A.json` (a `entries_by_member → [entries]` index keyed by leaf DWARF member name), then `scripts/base_tas.py` loads that JSON and either resolves a name only, or connects to TC397 over Infineon DAS/TAS and reads/writes the resolved address. There is no winIDEA / Wine / TASKING path here anymore — the earlier `tc397_tas_var.py`, `tc397_elfio_fast.py`, and `winidea_auto.py` were removed in the June refactor.

All paths below are relative to the repo root (`/home/shiheping/QianLiPrj/TC397Tools`).

## Run (agent path)

One command covers everything that does NOT need the probe + board:

```bash
.claude/skills/run-tc397tools/smoke.sh
```

It verifies the venv + `pyelftools`, `/opt/Tools/DAS/8.3.0/{lib/libdas_api.so,bin/tas_server}`, builds `scripts/libtc397_elfio_resolver.so` if missing, calls `base_tas.ensure_json_index()` (regenerates `build/MCU_A.json` if md5 doesn't match, otherwise warm-load <1 s), then `BaseTas.resolve_variable("VehModMngtGlbSafe1UsgModSts")` to confirm the leaf-to-full-path mapping is intact.

Overrides:

```bash
PYTHON=/path/to/venv/python   .claude/skills/run-tc397tools/smoke.sh
ELF=/path/to/app.elf          .claude/skills/run-tc397tools/smoke.sh
JSON=/path/to/index.json      .claude/skills/run-tc397tools/smoke.sh
MEMBER=g_someLeafName         .claude/skills/run-tc397tools/smoke.sh
DAS_HOME=/custom/das          .claude/skills/run-tc397tools/smoke.sh
```

The target-side path (`BaseTas.connect()` / `read_variable*` / `write_variable*`) needs the JDS probe + powered TC397. The driver does NOT exercise it because (a) without hardware, `connect_to_device` returns `0x00000004 (DEVICE_ACCESS)` on every selector, and (b) `python scripts/base_tas.py` performs a live `write_variable("VehModMngtGlbSafe1UsgModSts", value+1)` — it must only run on a bench you own. On the bench:

```bash
~/.virtualenvs/test_env/bin/python scripts/base_tas.py    # WRITES live MCU memory
```

For read-only target verification, import `BaseTas` instead and skip the write:

```bash
~/.virtualenvs/test_env/bin/python - <<'PY'
import sys; sys.path.insert(0, "scripts")
from base_tas import BaseTas
tas = BaseTas()
info = tas.resolve_variable("VehModMngtGlbSafe1UsgModSts")
try:
    tas.connect()
    print(hex(info.address), tas.read_variable_info_value(info))
finally:
    tas.disconnect()
PY
```

## Run (human path)

```bash
source ~/.local/bin/virtualenvwrapper.sh
workon test_env                           # Python 3.10 venv with pyelftools
bash scripts/build_tc397_elfio_resolver.sh    # once, or after C++/ELFIO changes
python scripts/base_tas.py                # interactive: resolve+read+WRITE+read
```

`scripts/base_tas.py` reads `DAS_HOME` from env, falling back to `/opt/Tools/DAS/8.3.0`. The hard-coded `ELF_PATH=/home/shiheping/.../Downloads/MCU_A.elf` and `JSON_PATH=/home/shiheping/.../build/MCU_A.json` are baked at the top of the file — pass overrides through `BaseTas(elf_path=..., json_path=...)` instead of editing globals.

## Prerequisites

- Ubuntu/Debian, x86_64. Verified on WSL2 Ubuntu 22.04 with `g++ 9.4.0`/Python 3.10.12.
- Python 3.10 venv at `~/.virtualenvs/test_env` with `pyelftools`. (`isystem.connect` is no longer required.)
- Infineon DAS 8.3.0 at `/opt/Tools/DAS/8.3.0` providing `lib/libdas_api.so` and `bin/tas_server` — install `DASTool/DAS_8.3.0_linux_x64.deb`.
- `g++` (any C++17) to build the resolver; headers come from the vendored `ELFIO/` directory.
- For target access: Infineon JDS / DAP miniWiggler probe (USB ID `058b:0043`) + powered TC397 board, with a udev rule `SUBSYSTEM=="usb", ATTR{idVendor}=="058b", ATTR{idProduct}=="0043", MODE="0666"`. `scripts/base_tas.py` auto-spawns `tas_server` on `127.0.0.1:24817`; no separate `trte`/Wine install needed.

## Build

The C++ resolver is the only build artifact:

```bash
bash scripts/build_tc397_elfio_resolver.sh
# → build/libtc397_elfio_resolver.so + scripts/libtc397_elfio_resolver.so (both copies are required)
```

Rebuild after any change under `cpp/` or `ELFIO/`. Both `.so` copies are gitignored (`.gitignore` has `*.so`). `base_tas.py` loads `scripts/libtc397_elfio_resolver.so` via ctypes — if `scripts/` is missing the copy, JSON generation raises `FileNotFoundError: C++ resolver not found: …` even though `build/` has one.

## Direct invocation

For internal changes to `base_tas.py`, drive the relevant function in isolation — no DAS, no target. From the repo root:

```bash
~/.virtualenvs/test_env/bin/python - <<'PY'
import sys, time; sys.path.insert(0, "scripts")
from base_tas import BaseTas, ensure_json_index, json_matches_elf

t = time.monotonic(); ensure_json_index(); print(f"index: {time.monotonic()-t:.2f}s, matches={json_matches_elf()}")

tas = BaseTas()
info = tas.resolve_variable("VehModMngtGlbSafe1UsgModSts")
print(info.name, hex(info.address), info.byte_size, info.type_name)

# Full-path lookup (no ambiguity check needed):
info2 = tas.resolve_variable("kAdapterReadSignalDoc.flexray.VehModMngtGlbSafe1UsgModSts")
assert info == info2

# Byte-indexed access:
info3 = tas.resolve_variable("kAdapterReadSignalDoc.flexray.VehModMngtGlbSafe1UsgModSts[0]")
print(info3.byte_size, info3.byte_offset)
PY
```

`resolve_variable` does not touch the C++ lib unless the JSON is missing or stale. To force a rebuild of the index, delete `build/MCU_A.json` (or corrupt its `elf_md5` field — the regeneration is keyed on md5, not mtime).

## Gotchas

- **The index contains DWARF *struct members*, not plain globals.** `entries_by_member` is built from DWARF members of composite types — every entry has the shape `parent.…​.leaf` (e.g. `kAdapterReadSignalDoc.flexray.VehModMngtGlbSafe1UsgModSts`). A flat global symbol that is *not* a struct member (e.g. an `int ACC_FAULTS;` defined at file scope) will NOT be in `entries_by_member` and `resolve_variable("ACC_FAULTS")` returns `KeyError: variable not found in JSON index`. The old `.symtab`-based tools would find it; this one won't. For flat globals, address-resolve via `nm`/`readelf` and use `BaseTas` with a hand-constructed `VariableInfo`, or use the `client.read(address, size)` API directly.
- **`python scripts/base_tas.py` writes to live MCU memory.** The `if __name__ == "__main__"` block reads `VehModMngtGlbSafe1UsgModSts`, calls `write_variable(...)` with `value + 1`, then re-reads. Run it only when you actually want that side effect on the bench. The README mentions this; it's easy to miss.
- **The resolver lib is loaded from `scripts/`, not `build/`.** `SO_PATH = REPO_ROOT / "libtc397_elfio_resolver.so"` and `REPO_ROOT = Path(__file__).parent`. The build script copies into both — don't `rm scripts/libtc397_elfio_resolver.so` and assume `build/` covers it.
- **JSON regeneration is keyed on md5, not mtime.** `json_matches_elf` recomputes the ELF md5 every call. Touching the ELF (re-stat) does not force a rebuild; corrupting `elf_md5` in the JSON does. On a 384 MB ELF, the md5 check itself takes ~0.5 s, and full regen takes ~40 s.
- **`MCU_A.json` survives without the ELF.** `ensure_json_index` returns the existing JSON when the ELF is absent and `json_is_usable` passes. That's useful for read-only deployment, but it means a stale index can hide a missing-ELF problem — `json_matches_elf` is `False` if either side is missing.
- **Leaf-name ambiguity raises at resolve time.** If a leaf member appears under multiple parent structs, `VariableIndex.resolve()` raises `ValueError: ambiguous member name 'X', matches=N: …`. Use the full dotted path (`base.sub.leaf`) in that case; `find()` returns the full list.
- **Examples under `examples/` are stale.** `examples/tc397_mcu_a_usage.py` and `examples/tc397_member_lookup_usage.py` still import from `scripts.tc397_tas_var` and `scripts.tc397_elfio_fast`, which were deleted in the June refactor. They will `ImportError` if run; the canonical example is the `__main__` block of `base_tas.py`.
- **DAS error `0x00000004 (DEVICE_ACCESS)` off the bench is normal.** With no probe, `BaseTas.connect()` (and the smoke driver if you ever add it) will hit `connect_to_device failed: 0x00000004 (DEVICE_ACCESS) (device='', id0=0x0)`. On the bench, the same code means the probe is up but the chip is unreachable — check power, reset, DAP wiring, or another client holding the target lock.
- **`test_com.py` and `usb_relay_control.py` at the repo root are unrelated.** They control a separate USB relay over `/dev/ttyACM0` / `/dev/ttyUSB0` and have nothing to do with TC397 / TAS / ELF.

## Troubleshooting

| Error | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'elftools'` | Wrong interpreter. Use `~/.virtualenvs/test_env/bin/python`, not system python. |
| `FileNotFoundError: C++ resolver not found: …/scripts/libtc397_elfio_resolver.so` | Run `bash scripts/build_tc397_elfio_resolver.sh` — needs `g++` and the vendored `ELFIO/` tree. |
| `FileNotFoundError: ELF file not found: …` from `generate_json_from_elf` | The hard-coded `ELF_PATH` points at `Downloads/MCU_A.elf`. Pass `elf_path=` to `BaseTas` / `ensure_json_index`, or drop the ELF in place. |
| `RuntimeError: …` from `tc397_elf_write_member_index` | DWARF parse failed — the message is the C++ buffer contents. Confirm the ELF is the TC397 build, not a host binary; rebuild the resolver if you just edited `cpp/`. |
| `OSError: /opt/Tools/DAS/8.3.0/lib/libdas_api.so: cannot open shared object file` | Install `DASTool/DAS_8.3.0_linux_x64.deb`, or pass `das_home=` / `DAS_HOME=…`. |
| `DasError: tas_server did not become ready` | Stale `tas_server` already running with a different config — `pkill -f tas_server` and retry. |
| `DasError: connect_to_device failed: 0x00000004 (DEVICE_ACCESS)` | Off-bench: expected. On-bench: probe present but chip unreachable — power, reset, DAP wiring, or another client holds the lock. |
| `KeyError: variable not found in JSON index: …` | Leaf name not in `entries_by_member`. Try a full dotted path; if the symbol really exists in the ELF but not the JSON, delete `build/MCU_A.json` to force a fresh build with the current `MAX_MEMBER_DEPTH = 8`. |
| `ValueError: ambiguous member name 'X', matches=N: …` | Resolve with the full `base.sub.leaf` path instead of the leaf alone. |
