#!/usr/bin/env python3
"""Example: TC397 TAS/DAS variable access with MCU_A.elf.

Run from the repository root inside the prepared virtualenv:

    source ~/.local/bin/virtualenvwrapper.sh
    workon test_env
    python examples/tc397_mcu_a_usage.py

This example intentionally does not implement PFlash programming. The current
free Linux TAS helper can connect through DAP miniWiggler and read/write target
memory by ELF variable name. Flash programming still needs a flash loader or a
vendor tool such as ADS/MemTool/winIDEA.
"""

from __future__ import annotations

from pathlib import Path
import sys
import time


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.tc397_tas_var import ElfParser, TasTool, Tc397VariableAccess


ELF_PATH = REPO_ROOT / "Downloads" / "MCU_A_0527.elf"
VARIABLE = "kAdapterReadSignalDoc.flexray.MobDevRPAReq1MobDevSts"


def flash_elf_example() -> None:
    """Placeholder showing where ELF programming would belong.

    PFlash programming is not just a memory write. It needs AURIX flash erase,
    page program, verify, and usually a RAM flash loader. That is not implemented
    by the free TAS variable-access helper yet.
    """

    raise NotImplementedError(
        "Free TAS variable helper does not implement ELF flash programming yet. "
        "Use Infineon ADS/MemTool or another licensed/debug tool for flashing."
    )


def main() -> None:
    elf = ElfParser(ELF_PATH, include_zero_size=True)
    # elf.parse()
    variable_ref = elf.get_variable_reference(VARIABLE)

    print("ELF variable resolved:")
    print(f"  expression: {VARIABLE}")
    print(f"  base name:  {variable_ref.variable.name}")
    print(f"  address:    0x{variable_ref.address:08x}")
    print(f"  byte size:  {variable_ref.default_byte_count}")

    tas = TasTool()

    # Flashing placeholder. Keep this explicit so automation code does not
    # accidentally treat variable memory access as safe flash programming.
    try:
        flash_elf_example()
    except NotImplementedError as exc:
        print(f"ELF flash step skipped: {exc}")

    with Tc397VariableAccess(ELF_PATH, tas_tool=tas, elf_parser=elf) as target:
        print("DAP miniWiggler connected.")

        start_time = time.perf_counter()
        old_data = None
        while time.perf_counter() - start_time < 100:
            new_data = target.read_reference_value(variable_ref)
            if new_data != old_data:
                # if len(new_data) <= 8:
                # new_value = int.from_bytes(new_data, "little", signed=False)
                print(f"Read {VARIABLE}: 0x{new_data:02x} ({new_data})")
                # else:
                #     preview = new_data[:32].hex()
                #     suffix = "..." if len(new_data) > 32 else ""
                #     print(f"Read {VARIABLE}: {len(new_data)} bytes {preview}{suffix}")
                old_data = new_data
            time.sleep(0.5)

        # print(f"Read {VARIABLE}: 0x{old_value:02x} ({old_value})")

        # # new_value = 0x12
        # # target.write_reference(variable_ref, new_value, byte_count=1, offset=0)
        # # print(f"Wrote {VARIABLE}: 0x{new_value:02x}")

        # read_back = target.read_reference(variable_ref, byte_count=1, offset=0)
        # print(f"Read back {VARIABLE}: {read_back.hex()}")

    print("DAP miniWiggler disconnected.")


if __name__ == "__main__":
    main()
