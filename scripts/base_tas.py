#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal TC397 TAS variable reader.

Features kept here:
1. call scripts/libtc397_elfio_resolver.so to generate MCU_A.json from ELF;
2. regenerate JSON when it does not match the ELF or is older than the ELF;
3. connect/disconnect TAS;
4. resolve a full member path or a leaf member name from JSON;
5. read the resolved address through TAS.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import time
from ctypes import (
    CDLL,
    CFUNCTYPE,
    POINTER,
    Structure,
    addressof,
    byref,
    c_char,
    c_char_p,
    c_uint,
    c_uint8,
    c_uint16,
    c_uint32,
    c_void_p,
    create_string_buffer,
)
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SO_PATH = REPO_ROOT / "scripts" / "libtc397_elfio_resolver.so"
ELF_PATH = REPO_ROOT / "Downloads" / "MCU_A.elf"
JSON_PATH = REPO_ROOT / "build" / "MCU_A.json"
MAX_MEMBER_DEPTH = 8

DEFAULT_DAS_HOME = "/opt/Tools/DAS/8.3.0"
DAS_MAX_PORT_TYPES = 64
DAS_MAX_SERVERS_PER_HOST = 16
DAS_MAX_TRANSFER_SIZE = 1024

DAS_PT_JTAG = 3
DAS_MPO_DEFAULT = 0
DAS_DIO_HOT_ATTACH = 0x00000000
DAS_AMAP_DEVICE_MIN = 0

DAS_TRA_R = 0x00
DAS_TRA_BYTE = 0x00
DAS_TRA_RW_TRANSACTION = 0x00
DAS_LC_DEFAULT = 0x00000000
DAS_LS_OK = 0x00000000
DAS_TS_OK = 0x00

DAS_ERROR_NAMES = {
    0x00000001: "DEVICE_RESET",
    0x00000002: "DEVICE_LOCKED",
    0x00000004: "DEVICE_ACCESS",
    0x00000008: "DEVICE_DATA",
    0x00000100: "PORT_ACCESS",
    0x00001000: "SERVER_LOCKED",
    0x00010000: "TIMEOUT",
    0x00080000: "COMMAND_FAILED",
    0x01000000: "PARAMETER",
    0x02000000: "CONNECTION",
    0x08000000: "NO_SERVER",
    0x80000000: "FATAL",
}


class DasServerInfo(Structure):
    _fields_ = [
        ("server_host_addr", c_char * 64),
        ("server_host_port", c_char * 32),
        ("name", c_char * 32),
        ("manufacturer_name", c_char * 32),
        ("version_major", c_uint8),
        ("version_minor", c_uint8),
        ("das_api_v_major", c_uint8),
        ("das_api_v_minor", c_uint8),
        ("server_lib_v_major", c_uint8),
        ("server_lib_v_minor", c_uint8),
        ("locked", c_uint8),
        ("reserved", c_uint8),
        ("date", c_char * 16),
        ("ports", c_uint8 * DAS_MAX_PORT_TYPES),
        ("process_id", c_uint32),
    ]


class DasServersOnHostList(Structure):
    _fields_ = [
        ("n_das_servers", c_uint32),
        ("si", DasServerInfo * DAS_MAX_SERVERS_PER_HOST),
    ]


class DasClientInfo(Structure):
    _fields_ = [
        ("name", c_char * 32),
        ("manufacturer_name", c_char * 32),
        ("version_major", c_uint8),
        ("version_minor", c_uint8),
        ("das_api_v_major", c_uint8),
        ("das_api_v_minor", c_uint8),
        ("date", c_char * 16),
        ("process_id", c_uint32),
    ]


class DasDeviceId(Structure):
    _fields_ = [("id0", c_uint32), ("id1", c_uint32)]


class DasDeviceInfo(Structure):
    _fields_ = [
        ("id", DasDeviceId),
        ("name", c_char * 32),
        ("byte_order", c_uint8 * 64),
    ]


class DasKey(Structure):
    _fields_ = [("key", c_uint32 * 4)]


class DasTransaction(Structure):
    _fields_ = [
        ("action", c_uint8),
        ("addr_map", c_uint8),
        ("n_bytes", c_uint16),
        ("status", c_uint8),
        ("error", c_uint8),
        ("n_bytes_ok", c_uint16),
        ("address", c_uint32),
        ("data", c_void_p),
    ]


class DasList(Structure):
    _fields_ = [
        ("control", c_uint32),
        ("status", c_uint32),
        ("n_items", c_uint8),
        ("transaction", POINTER(DasTransaction)),
    ]


class DasApi(Structure):
    _fields_ = [
        ("name", c_char_p),
        ("manufacturer_name", c_char_p),
        ("version_major", c_uint8),
        ("version_minor", c_uint8),
        ("das_api_v_major", c_uint8),
        ("das_api_v_minor", c_uint8),
        ("date", c_char_p),
        ("init_flag", c_uint8),
        ("cancel_lists", c_void_p),
        ("mcd_chl_open_f", c_void_p),
        ("close_port", c_void_p),
        ("connect_to_device", c_void_p),
        ("disconnect_device", c_void_p),
        ("exec_config_list", c_void_p),
        ("exit", c_void_p),
        ("mcd_send_msg_f", c_void_p),
        ("get_das_servers", c_void_p),
        ("get_ids_servers", c_void_p),
        ("mcd_receive_msg_f", c_void_p),
        ("init", c_void_p),
        ("init_device", c_void_p),
        ("mcd_chl_reset_f", c_void_p),
        ("map_port", c_void_p),
        ("mcd_chl_close_f", c_void_p),
        ("open_port", c_void_p),
        ("mcd_qry_error_info_f", c_void_p),
        ("mcd_qry_input_handle_f", c_void_p),
        ("send_list", c_void_p),
        ("tas_fexec", c_void_p),
        ("sleep", c_void_p),
        ("spawn_port", c_void_p),
        ("start_server", c_void_p),
        ("terminate_server", c_void_p),
        ("wait_list", c_void_p),
    ]


@dataclass(frozen=True)
class VariableInfo:
    name: str
    address: int
    byte_size: int
    signed: bool | None = None
    base_name: str = ""
    byte_offset: int = 0
    type_name: str = ""


class DasError(RuntimeError):
    pass


def _describe_error(error: int) -> str:
    names = [name for bit, name in DAS_ERROR_NAMES.items() if error & bit]
    return f"0x{error:08x}" + (f" ({'|'.join(names)})" if names else "")


def _load_elf_resolver() -> ctypes.CDLL:
    if not SO_PATH.exists():
        raise FileNotFoundError(f"C++ resolver not found: {SO_PATH}")
    lib = ctypes.CDLL(str(SO_PATH))
    lib.tc397_elf_write_member_index.argtypes = [
        c_char_p,
        c_char_p,
        ctypes.c_int,
        c_char_p,
        ctypes.c_size_t,
    ]
    lib.tc397_elf_write_member_index.restype = ctypes.c_int
    return lib


def generate_json_from_elf(elf_path: Path = ELF_PATH, json_path: Path = JSON_PATH) -> Path:
    if not elf_path.exists():
        raise FileNotFoundError(f"ELF file not found: {elf_path}")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    err = create_string_buffer(4096)
    rc = _load_elf_resolver().tc397_elf_write_member_index(
        str(elf_path).encode(),
        str(json_path).encode(),
        MAX_MEMBER_DEPTH,
        err,
        len(err),
    )
    if rc != 0:
        raise RuntimeError(err.value.decode(errors="replace"))
    return json_path


def json_matches_elf(elf_path: Path = ELF_PATH, json_path: Path = JSON_PATH) -> bool:
    if not elf_path.exists() or not json_path.exists():
        return False
    if elf_path.stat().st_mtime > json_path.stat().st_mtime:
        return False
    try:
        with json_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return False
    return (
        Path(str(data.get("elf_path", ""))).resolve() == elf_path.resolve()
        and int(data.get("max_depth", -1)) == MAX_MEMBER_DEPTH
        and isinstance(data.get("entries_by_member"), dict)
    )


def ensure_json_index(elf_path: Path = ELF_PATH, json_path: Path = JSON_PATH) -> Path:
    if not json_matches_elf(elf_path, json_path):
        return generate_json_from_elf(elf_path, json_path)
    return json_path


class VariableIndex:
    def __init__(self, json_path: Path = JSON_PATH) -> None:
        with json_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        self.entries_by_member: dict[str, list[dict]] = data["entries_by_member"]
        self.entries_by_expression: dict[str, dict] = {}
        for entries in self.entries_by_member.values():
            for item in entries:
                self.entries_by_expression[item["expression"]] = item

    def find(self, name: str) -> list[VariableInfo]:
        base_name, byte_index = self._split_byte_index(name)
        if "." in base_name:
            entries = [self.entries_by_expression[base_name]] if base_name in self.entries_by_expression else []
        else:
            entries = self.entries_by_member.get(base_name, [])
        return [self._to_variable_info(item, byte_index) for item in entries]

    def resolve(self, name: str) -> VariableInfo:
        matches = self.find(name)
        if not matches:
            raise KeyError(f"variable not found in JSON index: {name}")
        if len(matches) > 1:
            preview = ", ".join(item.name for item in matches[:10])
            raise ValueError(f"ambiguous member name '{name}', matches={len(matches)}: {preview}")
        return matches[0]

    @staticmethod
    def _split_byte_index(name: str) -> tuple[str, int | None]:
        if name.endswith("]") and "[" in name:
            base, index = name.rsplit("[", 1)
            return base, int(index[:-1], 0)
        return name, None

    @staticmethod
    def _to_variable_info(item: dict, byte_index: int | None) -> VariableInfo:
        address = int(item["address"])
        byte_size = int(item.get("byte_size") or 0)
        if byte_index is not None:
            if byte_index < 0 or (byte_size and byte_index >= byte_size):
                raise ValueError(f"byte index {byte_index} outside {item['expression']} size {byte_size}")
            address += byte_index
            byte_size = 1
        return VariableInfo(
            name=str(item["expression"]) + (f"[{byte_index}]" if byte_index is not None else ""),
            address=address,
            byte_size=byte_size,
            signed=item.get("signed") if isinstance(item.get("signed"), bool) else None,
            base_name=str(item.get("base_name", "")),
            byte_offset=int(item.get("byte_offset") or 0) + (byte_index or 0),
            type_name=str(item.get("type_name", "")),
        )


class DasClient:
    def __init__(
        self,
        das_home: str,
        host: str,
        server_index: int,
        port_type: int,
        port_sel: int,
        device_sel: int,
        addr_map: int,
    ) -> None:
        self.host = host
        self.server_index = server_index
        self.port_type = port_type
        self.port_sel = port_sel
        self.device_sel = device_sel
        self.addr_map = addr_map
        self.port = None

        lib_path = Path(das_home) / "lib" / "libdas_api.so"
        self.lib = CDLL(str(lib_path))
        self.lib.das_api_load.argtypes = [c_uint, POINTER(c_uint)]
        self.lib.das_api_load.restype = POINTER(DasApi)

        error = c_uint(0)
        api_ptr = self.lib.das_api_load(4, byref(error))
        if not api_ptr or error.value:
            raise DasError(f"das_api_load failed: {_describe_error(error.value)}")
        api = api_ptr.contents

        self._init = CFUNCTYPE(None, POINTER(DasClientInfo), POINTER(c_uint))(api.init)
        self._get_servers = CFUNCTYPE(None, c_char_p, POINTER(DasServersOnHostList), POINTER(c_uint))(api.get_das_servers)
        self._open_port = CFUNCTYPE(c_void_p, c_uint, POINTER(DasServerInfo), POINTER(DasKey), POINTER(c_uint))(api.open_port)
        self._map_port = CFUNCTYPE(None, c_void_p, c_uint, c_uint, c_uint, POINTER(c_uint))(api.map_port)
        self._connect_to_device = CFUNCTYPE(None, c_void_p, c_uint8, POINTER(DasDeviceInfo), POINTER(c_uint))(api.connect_to_device)
        self._init_device = CFUNCTYPE(None, c_void_p, c_void_p, c_uint, POINTER(c_uint))(api.init_device)
        self._close_port = CFUNCTYPE(None, c_void_p, POINTER(c_uint))(api.close_port)
        self._send_list = CFUNCTYPE(None, c_void_p, POINTER(DasList), POINTER(c_uint))(api.send_list)
        self._wait_list = CFUNCTYPE(None, c_void_p, c_uint, POINTER(DasList), POINTER(c_uint))(api.wait_list)

        info = DasClientInfo()
        info.name = b"TC397Tools"
        info.manufacturer_name = b"Local"
        info.version_major = 0
        info.version_minor = 1
        info.das_api_v_major = 4
        info.das_api_v_minor = 3
        info.date = b"Jun 01 2026"

        error = c_uint(0)
        self._init(byref(info), byref(error))
        if error.value:
            raise DasError(f"das init failed: {_describe_error(error.value)}")

    def servers(self) -> DasServersOnHostList:
        servers = DasServersOnHostList()
        error = c_uint(0)
        self._get_servers(self.host.encode(), byref(servers), byref(error))
        if error.value:
            raise DasError(f"get_das_servers failed: {_describe_error(error.value)}")
        return servers

    def open(self) -> DasDeviceInfo:
        servers = self.servers()
        if self.server_index >= servers.n_das_servers:
            raise DasError(f"server index {self.server_index} out of range")
        server = servers.si[self.server_index]
        if self.port_sel >= server.ports[self.port_type]:
            raise DasError(f"port select {self.port_sel} out of range")

        key = DasKey((c_uint32 * 4)(0, 0, 0, 0))
        error = c_uint(0)
        self.port = self._open_port(0, byref(server), byref(key), byref(error))
        if not self.port or error.value:
            raise DasError(f"open_port failed: {_describe_error(error.value)}")

        error = c_uint(0)
        self._map_port(self.port, DAS_MPO_DEFAULT, self.port_type, self.port_sel, byref(error))
        if error.value:
            self.close()
            raise DasError(f"map_port failed: {_describe_error(error.value)}")

        device = DasDeviceInfo()
        error = c_uint(0)
        self._connect_to_device(self.port, self.device_sel, byref(device), byref(error))
        if error.value:
            self.close()
            raise DasError(f"connect_to_device failed: {_describe_error(error.value)}")

        error = c_uint(0)
        self._init_device(self.port, None, DAS_DIO_HOT_ATTACH, byref(error))
        if error.value:
            self.close()
            raise DasError(f"init_device failed: {_describe_error(error.value)}")
        return device

    def close(self) -> None:
        if self.port:
            error = c_uint(0)
            self._close_port(self.port, byref(error))
            self.port = None

    def read(self, address: int, byte_count: int) -> bytes:
        if not self.port:
            raise DasError("TAS is not connected")
        chunks = []
        offset = 0
        while offset < byte_count:
            size = min(DAS_MAX_TRANSFER_SIZE, byte_count - offset)
            buffer = create_string_buffer(size)
            self._read_once(address + offset, buffer, size)
            chunks.append(buffer.raw)
            offset += size
        return b"".join(chunks)

    def _read_once(self, address: int, buffer, size: int) -> None:
        tx = DasTransaction()
        tx.action = DAS_TRA_R | DAS_TRA_BYTE | DAS_TRA_RW_TRANSACTION
        tx.addr_map = self.addr_map
        tx.n_bytes = size
        tx.address = address & 0xFFFFFFFF
        tx.data = c_void_p(addressof(buffer))

        tx_array = (DasTransaction * 1)(tx)
        tx_list = DasList()
        tx_list.control = DAS_LC_DEFAULT
        tx_list.status = DAS_LS_OK
        tx_list.n_items = 1
        tx_list.transaction = tx_array

        error = c_uint(0)
        self._send_list(self.port, byref(tx_list), byref(error))
        if error.value:
            raise DasError(f"send_list failed: {_describe_error(error.value)}")

        error = c_uint(0)
        self._wait_list(self.port, 10000, byref(tx_list), byref(error))
        done = tx_list.transaction[0]
        if error.value or tx_list.status != DAS_LS_OK or done.status != DAS_TS_OK:
            raise DasError(
                f"read failed: api={_describe_error(error.value)} "
                f"list=0x{tx_list.status:x} tx=0x{done.status:x} "
                f"tx_error={_describe_error(done.error)}"
            )


class BaseTas:
    def __init__(
        self,
        elf_path: Path = ELF_PATH,
        json_path: Path = JSON_PATH,
        das_home: str | Path = os.environ.get("DAS_HOME", DEFAULT_DAS_HOME),
    ) -> None:
        self.elf_path = elf_path
        self.json_path = json_path
        self.das_home = str(das_home)
        self.index: VariableIndex | None = None
        self.client: DasClient | None = None
        self.tas_process: subprocess.Popen | None = None

    def prepare_index(self) -> VariableIndex:
        ensure_json_index(self.elf_path, self.json_path)
        self.index = VariableIndex(self.json_path)
        return self.index

    def connect(self) -> DasDeviceInfo:
        self._ensure_tas_server()
        self.disconnect()
        self.client = DasClient(
            das_home=self.das_home,
            host="127.0.0.1",
            server_index=0,
            port_type=DAS_PT_JTAG,
            port_sel=0,
            device_sel=0,
            addr_map=DAS_AMAP_DEVICE_MIN,
        )
        return self.client.open()

    def disconnect(self) -> None:
        if self.client:
            self.client.close()
            self.client = None

    def resolve_variable(self, name: str) -> VariableInfo:
        if self.index is None:
            self.prepare_index()
        assert self.index is not None
        return self.index.resolve(name)

    def read_variable(self, name: str, byte_count: int | None = None) -> bytes:
        if not self.client:
            raise DasError("TAS is not connected; call connect() first")
        info = self.resolve_variable(name)
        size = byte_count or info.byte_size
        if size <= 0:
            raise ValueError(f"unknown byte size for {info.name}; pass byte_count")
        return self.client.read(info.address, size)

    def read_variable_value(self, name: str, byte_count: int | None = None) -> int:
        info = self.resolve_variable(name)
        data = self.read_variable(name, byte_count)
        return int.from_bytes(data, "little", signed=bool(info.signed))

    def _ensure_tas_server(self) -> None:
        probe = DasClient(
            das_home=self.das_home,
            host="127.0.0.1",
            server_index=0,
            port_type=DAS_PT_JTAG,
            port_sel=0,
            device_sel=0,
            addr_map=DAS_AMAP_DEVICE_MIN,
        )
        try:
            if probe.servers().n_das_servers:
                return
        except DasError:
            pass

        cmd = Path(self.das_home) / "bin" / "tas_server"
        self.tas_process = subprocess.Popen(
            [str(cmd) if cmd.exists() else "tas_server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            time.sleep(0.2)
            try:
                if probe.servers().n_das_servers:
                    return
            except DasError:
                pass
        raise DasError("tas_server did not become ready")

if __name__ == "__main__":
    tas = BaseTas()
    info = tas.resolve_variable("VehModMngtGlbSafe1UsgModSts")
    print(f"name={info.name} address=0x{info.address:08x} size={info.byte_size} type={info.type_name}")
    try:
        tas.connect()
        data = tas.read_variable("VehModMngtGlbSafe1UsgModSts", None)
        value = int.from_bytes(data, "little", signed=bool(info.signed))
        print(f"bytes={data.hex()} value={value} hex=0x{value:x}")
    finally:
        tas.disconnect()
