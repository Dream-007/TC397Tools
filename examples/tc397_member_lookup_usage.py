#!/usr/bin/env python3
"""Example: reverse lookup full DWARF member paths by leaf member name.

The slow step is done once by the C++ ELFIO resolver:

    ELF/DWARF -> build/MCU_A_0527.member_index.json

After that Python only reads JSON and can resolve a leaf name such as
VehModMngtGlbSafe1UsgModSts to full expressions such as
kAdapterReadSignalDoc.flexray.VehModMngtGlbSafe1UsgModSts.
"""

from __future__ import annotations

from pathlib import Path
import sys
import time


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.tc397_elfio_fast import MemberIndex, write_member_index


ELF_PATH = REPO_ROOT / "Downloads" / "MCU_A_0527.elf"
INDEX_PATH = REPO_ROOT / "build" / "MCU_A_0527.member_index.json"
MEMBER_NAME = "MobDevRPAReq1MobDevSts"
MAX_DEPTH = 8


def ensure_member_index() -> Path:
    if INDEX_PATH.exists() and INDEX_PATH.stat().st_mtime >= ELF_PATH.stat().st_mtime:
        return INDEX_PATH

    start = time.perf_counter()
    output = write_member_index(ELF_PATH, INDEX_PATH, max_depth=MAX_DEPTH)
    elapsed = time.perf_counter() - start
    print(f"generated member index: {output} ({elapsed:.2f}s)")
    return output


def main() -> None:
    index_path = ensure_member_index()

    start = time.perf_counter()
    member_index = MemberIndex(index_path)
    matches = member_index.find(MEMBER_NAME)
    elapsed = time.perf_counter() - start

    print(f"lookup member: {MEMBER_NAME}")
    print(f"matches: {len(matches)} ({elapsed:.3f}s)")

    for item in matches[:20]:
        print(
            f"  {item['expression']} "
            f"addr=0x{int(item['address']):08x} "
            f"size={item['byte_size']}"
        )


if __name__ == "__main__":
    main()
