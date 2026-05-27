#!/usr/bin/env python3
"""Small winIDEA automation CLI for flashing and variable access.

Run inside the prepared virtualenv:
    workon test_env
    python scripts/winidea_auto.py --help
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import isystem.connect as ic


DEFAULT_INSTANCE_ID = "tc397_auto"


def _value_to_python(value: ic.CValueType) -> Any:
    if value.isError():
        raise RuntimeError(value.getResult())
    if value.isTypeFloat():
        return value.getDouble()
    if value.isTypeSigned() or value.isTypeUnsigned():
        return value.getLong()
    return value.getResult()


def _connect(args: argparse.Namespace) -> ic.ConnectionMgr:
    cfg = ic.CConnectionConfig()
    cfg.instanceId(args.instance_id)
    cfg.start(ic.IConnect.lfStartIfRequired)
    cfg.waitTime(ic.IConnect.lfWait10s)

    if args.workspace:
        cfg.workspace(str(Path(args.workspace).expanduser().resolve()))
    if args.headless:
        cfg.visibility(ic.IConnect.lfShowHidden)

    exe_dir = args.winidea_dir or os.environ.get("WINIDEA_EXE_DIR")
    if exe_dir:
        cfg.exe_dir(str(Path(exe_dir).expanduser().resolve()))

    conn_mgr = ic.ConnectionMgr()
    conn_mgr.connect(cfg)
    return conn_mgr


def _workspace_controller(conn_mgr: ic.ConnectionMgr) -> ic.CWorkspaceController:
    return ic.CWorkspaceController(conn_mgr)


def create_workspace(args: argparse.Namespace) -> None:
    conn_mgr = _connect(args)
    ws_ctrl = _workspace_controller(conn_mgr)

    cfg = ic.CCfg_debug_basic()
    cfg.set_Device(args.device)
    cfg.set_SymPath(str(Path(args.elf).expanduser().resolve()))
    cfg.set_UseSymForPgm(True)
    cfg.set_CreateSMP(args.create_smp)
    if args.probe:
        cfg.set_Probe(args.probe)

    ws_path = Path(args.output).expanduser().resolve()
    ws_path.parent.mkdir(parents=True, exist_ok=True)
    ws_ctrl.create(str(ws_path), "", cfg)

    opt_hw = ic.COptionController(conn_mgr, "/iOPEN/Hardware")
    opt_hw.set("Emulator", args.emulator)

    if args.emulator.upper() == "IFX_DAS":
        opt_comm = ic.COptionController(conn_mgr, "/iOPEN/Communication")
        opt_comm.set("Mode", "DAS")
    elif args.usb:
        opt_comm = ic.COptionController(conn_mgr, "/iOPEN/Communication")
        opt_comm.set_multi({"Mode": "USB", "USBDeviceName": args.usb_device})

    opt_debug = ic.COptionController(conn_mgr, "/iOPEN/Emulation.Debugging")
    opt_debug.set("DebugChannel", args.debug_channel)

    opt_tri = ic.COptionController(conn_mgr, "/iOPEN/Emulation.OCD_TRICORE")
    opt_tri.set_multi(
        {
            "DebugMode": args.debug_channel,
            "DAPClock": str(args.dap_clock_khz),
        }
    )

    ws_ctrl.saveAs(str(ws_path))
    print(f"Created workspace: {ws_path}")


def flash(args: argparse.Namespace) -> None:
    conn_mgr = _connect(args)
    if args.elf:
        elf_path = str(Path(args.elf).expanduser().resolve())
        cfg_ctrl = ic.CConfigurationController(conn_mgr)
        ic.COptionController(
            conn_mgr, "/IDE/System.Debug.Applications[0].SymbolFiles.File"
        ).clear()
        ic.COptionController(conn_mgr, "/IDE/System.Debug.SoCs[0].DLFs_Program.File").clear()
        cfg_ctrl.ide_app_files("").add_file(elf_path, "Elf", 0)
        cfg_ctrl.ide_session().SoC("").program_files().add_file(elf_path, "Elf", 0)
        _workspace_controller(conn_mgr).save()

    if args.mode == "session":
        sess_ctrl = ic.CSessionCtrl(conn_mgr)
        sess_ctrl.begin_program()
        if args.detach:
            sess_ctrl.end()
    else:
        ic.CDebugFacade(conn_mgr).download()

    print("Flash/program operation completed.")


def read_var(args: argparse.Namespace) -> None:
    conn_mgr = _connect(args)
    data_ctrl = ic.CDataController(conn_mgr)
    value = data_ctrl.evaluate(args.access, args.name)
    print(_value_to_python(value))


def write_var(args: argparse.Namespace) -> None:
    conn_mgr = _connect(args)
    data_ctrl = ic.CDataController(conn_mgr)
    result = data_ctrl.modify(args.access, args.name, args.value, args.read_back)
    if args.read_back:
        print(result)


def address(args: argparse.Namespace) -> None:
    conn_mgr = _connect(args)
    info = ic.CAddressController(conn_mgr).getSymbolInfo(args.access, args.name)
    print(
        f"name={args.name} mem_area={info.getMemArea()} "
        f"address=0x{info.getAddress():x} size_maus={info.getSizeMAUs()}"
    )


def version(args: argparse.Namespace) -> None:
    conn_mgr = _connect(args)
    print(f"winIDEA: {conn_mgr.getWinIDEAVersion()}")
    print(f"isystem.connect: {conn_mgr.getIConnectDllVersion()}")


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", "-w", help="Path to .xjrf workspace.")
    parser.add_argument("--instance-id", default=DEFAULT_INSTANCE_ID)
    parser.add_argument("--winidea-dir", help="Directory containing winIDEA executable.")
    parser.add_argument("--headless", action="store_true", help="Start winIDEA hidden.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="winIDEA Python automation helper")
    _add_common(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create-workspace")
    create.add_argument("--output", "-o", required=True)
    create.add_argument("--elf", required=True)
    create.add_argument("--device", default="TC397XP")
    create.add_argument("--emulator", default="IFX_DAS")
    create.add_argument("--probe")
    create.add_argument("--debug-channel", default="DAP", choices=["DAP", "JTAG"])
    create.add_argument("--dap-clock-khz", type=int, default=2000)
    create.add_argument("--create-smp", action="store_true", default=True)
    create.add_argument("--usb", action="store_true")
    create.add_argument("--usb-device", default="")
    create.set_defaults(func=create_workspace)

    flash_parser = subparsers.add_parser("flash")
    flash_parser.add_argument("--elf", help="Override workspace ELF before programming.")
    flash_parser.add_argument("--mode", choices=["session", "download"], default="session")
    flash_parser.add_argument("--detach", action="store_true")
    flash_parser.set_defaults(func=flash)

    read_parser = subparsers.add_parser("read")
    read_parser.add_argument("name", help="C expression or variable name.")
    read_parser.add_argument("--access", type=int, default=ic.IConnectDebug.fRealTime)
    read_parser.set_defaults(func=read_var)

    write_parser = subparsers.add_parser("write")
    write_parser.add_argument("name", help="Writable C expression or variable name.")
    write_parser.add_argument("value", help="Value expression, e.g. 1, 0x10, true.")
    write_parser.add_argument("--access", type=int, default=ic.IConnectDebug.fRealTime)
    write_parser.add_argument("--no-read-back", dest="read_back", action="store_false")
    write_parser.set_defaults(func=write_var, read_back=True)

    addr_parser = subparsers.add_parser("address")
    addr_parser.add_argument("name", help="Symbol/expression name.")
    addr_parser.add_argument("--access", type=int, default=ic.IConnectDebug.fRealTime)
    addr_parser.set_defaults(func=address)

    ver_parser = subparsers.add_parser("version")
    ver_parser.set_defaults(func=version)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
