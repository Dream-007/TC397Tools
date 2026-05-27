# winIDEA Linux Setup

Installed user-level components:

- winIDEA portable: `~/.local/opt/winidea-9.21.408`
- Current symlink: `~/.local/opt/winidea`
- Launcher symlink: `~/.local/bin/winidea`
- Python SDK in `test_env`: `isystem.connect==9.21.408.1`

The remaining OS dependencies require sudo on this WSL Ubuntu host:

```bash
scripts/install_winidea_deps_ubuntu.sh
```

After that, open a new shell or run:

```bash
source scripts/winidea_env.sh
```

Basic usage:

```bash
workon test_env

python scripts/winidea_auto.py --workspace path/to/project.xjrf flash --elf path/to/app.elf
python scripts/winidea_auto.py --workspace path/to/project.xjrf read mainLoopCounter
python scripts/winidea_auto.py --workspace path/to/project.xjrf write mainLoopCounter 42
python scripts/winidea_auto.py --workspace path/to/project.xjrf address mainLoopCounter
```

Create a starter TC397 workspace:

```bash
python scripts/winidea_auto.py create-workspace \
  --output workspaces/tc397_auto.xjrf \
  --elf path/to/app.elf \
  --device TC397XP \
  --emulator IFX_DAS \
  --debug-channel DAP \
  --dap-clock-khz 2000
```

winIDEA target debugging, flashing, and hardware access may still require a valid TASKING/iSYSTEM license or a license-free mode explicitly provided by the vendor for your hardware/use case. This setup does not bypass licensing.

## Infineon DAS JDS Debug / COM

The TC397 application kit enumerates as:

```text
058b:0043 Infineon Technologies DAS JDS Application Kit TC397 V1.0
```

It exposes two different paths:

- `Infineon DAS JDS Debug`: debug/programming probe used by winIDEA through Infineon DAS/TAS.
- `Infineon DAS JDS COM`: UART/terminal bridge, exposed on this machine as `/dev/ttyUSB0`.

GUI setup in winIDEA:

1. `File | Workspace | New Workspace`, or `Hardware | Select target`.
2. Select the Infineon TC397 application kit target if listed.
3. If configuring manually, use:
   - CPU/SoC: `TC397XP` or the exact TC397 variant on your board.
   - Probe/System: `System`, not Active Probe.
   - Emulator/Hardware: `IFX_DAS` / Infineon DAS.
   - Communication: `DAS`.
   - Debug channel: usually `DAP`; use `JTAG` only if your board/workspace is wired/configured that way.
   - Start with a conservative clock, for example `2000 kHz`.
4. Add your ELF under program/download files and symbol files, then run `Debug | Download` or `Debug | Program`.

Serial console:

```bash
sudo usermod -aG dialout "$USER"
```

Restart the WSL session after adding the group, then use `/dev/ttyUSB0` with your board's UART baud rate, for example:

```bash
picocom -b 115200 /dev/ttyUSB0
```

or:

```bash
python -m serial.tools.miniterm /dev/ttyUSB0 115200
```

If winIDEA cannot connect to the Debug side, check:

```bash
lsusb | grep -i infineon
ls -l /dev/ttyUSB0
cat /etc/udev/rules.d/90-infineon-tas.rules
```

Expected udev rule:

```text
SUBSYSTEM=="usb", ATTR{idVendor}=="058b", ATTR{idProduct}=="0043", MODE="0666"
```

Generated workspace:

- `workspaces/tc397_das_jds.xjrf`
- `workspaces/tc397_das_jds.device.json`

This workspace is configured for the detected JDS:

```text
ID 058b:0043 Infineon Technologies DAS JDS Application Kit TC397 V1.0
Serial: AK9D8ARF
```

The Linux bus/device number is not stored inside the workspace because it changes after reconnects. During setup it was seen as `001/008`, later as `001/009`, `001/010`, and `001/011`; the stable identity is `058b:0043` plus serial `AK9D8ARF`. The workspace uses the stable winIDEA/DAS configuration instead: `TC397XX`, `IFX_DAS`, `TCP/IP`, `127.0.0.1:24817`, `DAP`, `DAP_W`.

For Infineon DAP miniWiggler/JDS on Linux or WSL, start `tas_server` first and use winIDEA `Hardware -> Debugger Hardware -> Communication -> TCP/IP` with IP address `127.0.0.1` and port `24817`. The `USB` device list in winIDEA is the wrong path in this setup and may be empty even when Linux can see the probe.

If winIDEA reports `No USB devices found` with `BBClient` in the ASYST log, the workspace is using a TASKING BlueBox hardware entry instead of Infineon DAS. For this JDS workspace, the `HServerData/HW/HW` value must remain `14`; values such as `8` make winIDEA search for iC7/iC5700 BlueBox USB hardware.

If winIDEA reports `License acquisition failed` / `License Error 1: No valid license is available`, the probe and TAS path are already past the USB discovery stage and the next blocker is TASKING/iSYSTEM licensing. Configure a legitimate TASKING license through winIDEA/TLM or environment variables such as `TSK_LICENSE_SERVER`, `TSK_LICENSE_FILE`, or `TSK_LICENSE_KEY`.

## Free Linux TAS variable access

`scripts/tc397_tas_var.py` is a winIDEA-free helper for the first automation stage: resolve a variable name from an ELF file, then read or write the target address through the Linux TAS/DAS server.

Run it from the prepared Python virtualenv:

```bash
source ~/.local/bin/virtualenvwrapper.sh
workon test_env
python scripts/tc397_tas_var.py servers
python scripts/tc397_tas_var.py probe --scan
python scripts/tc397_tas_var.py variables path/to/app.elf --limit 50
python scripts/tc397_tas_var.py variables path/to/app.elf --name-contains g_ --json
python scripts/tc397_tas_var.py symbol path/to/app.elf g_someVariable
python scripts/tc397_tas_var.py read path/to/app.elf g_someVariable
python scripts/tc397_tas_var.py write path/to/app.elf g_someVariable 42
python scripts/tc397_tas_var.py read path/to/app.elf 'g_buffer[11]'
python scripts/tc397_tas_var.py write path/to/app.elf 'g_buffer[11]' 0x12
```

Large AURIX ELF files can contain very large DWARF sections. The parser defaults to `.symtab` variable parsing only, which is usually enough for address and byte size. Add `--with-dwarf` only when type names or signedness are needed and you are prepared for a much slower parse.

Byte indexing is supported for variables with array/buffer-like storage. `g_buffer[11]` means byte offset `11` from `g_buffer`'s base address and defaults to reading or writing one byte. Quote the expression in the shell so brackets are not interpreted by the shell.

The script exposes `ElfVariable` and `ElfVariableTable` for debugging ELF contents in Python. `ElfVariableTable.from_elf(path).variables` returns the full list, `.by_name` returns a lossless dictionary of name to all matches, and `.best_by_name` returns one preferred variable per name for simple read/write operations.

For Python automation, use the class interfaces:

```python
from scripts.tc397_tas_var import ElfParser, TasTool, Tc397VariableAccess

elf = ElfParser("path/to/app.elf")
address, size = elf.get_variable_address_size("g_someVariable")
all_variables = elf.variables
variables_by_name = elf.by_name

tas = TasTool()
with Tc397VariableAccess("path/to/app.elf", tas_tool=tas) as target:
    raw = target.read_variable("g_someVariable")
    value = target.read_variable_value("g_someVariable")
    target.write_variable("g_someVariable", 42)
    byte11 = target.read_variable_value("g_buffer[11]", byte_count=1)
    target.write_variable("g_buffer[11]", 0x12)
```

The script depends on `pyelftools` and `/opt/Tools/DAS/8.3.0/lib/libdas_api.so`. It does not require a winIDEA license. If `probe` reports `DEVICE_ACCESS`, TAS can see the miniWiggler but the target cannot currently be accessed; check target power, reset state, DAP/JTAG wiring, and whether another client has the target locked.
