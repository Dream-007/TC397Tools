#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal TC397 TAS variable reader.

Features kept here:
1. call scripts/libtc397_elfio_resolver.so to generate MCU_A.json from ELF;
2. regenerate JSON when it does not match the ELF or is older than the ELF;
3. connect/disconnect TAS;
4. resolve a full member path or a leaf member name from JSON;
5. read/write the resolved address through TAS.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import struct
import tarfile
import time
import zipfile
from typing import Iterable, Optional
from urllib.parse import unquote, urlparse

import requests
import ctypes
import hashlib
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


REPO_ROOT = Path(__file__).parent
SO_PATH = REPO_ROOT / "libtc397_elfio_resolver.so"
ELF_PATH = Path('/home/shiheping/QianLiPrj/platform_auto_test/testcase/SigAccess/P_30T/case_helper/MCUA/MCU_A.elf')
JSON_PATH = Path('/home/shiheping/QianLiPrj/platform_auto_test/testcase/SigAccess/P_30T/case_helper/MCUA/MCU_A.json')
MAX_MEMBER_DEPTH = 8

DEFAULT_DAS_HOME = "/opt/Tools/DAS/8.3.0"
DAS_MAX_PORT_TYPES = 64
DAS_MAX_SERVERS_PER_HOST = 16
DAS_MAX_TRANSFER_SIZE = 1024

DAS_PT_JTAG = 3
DAS_MPO_DEFAULT = 0
DAS_DIO_HOT_ATTACH = 0x00000000
DAS_AMAP_DEVICE_MIN = 0
INFINEON_USB_VENDOR_ID = "058b"

DAS_TRA_R = 0x00
DAS_TRA_W = 0x01
DAS_TRA_BYTE = 0x00
DAS_TRA_RW_TRANSACTION = 0x00
DAS_LC_DEFAULT = 0x00000000
DAS_LS_OK = 0x00000000
DAS_TS_OK = 0x00

SIGNAL_DATA_FLOAT = 0
SIGNAL_DATA_UINT32 = 1
SIGNAL_DATA_INT32 = 2
SIGNAL_DATA_UINT16 = 3
SIGNAL_DATA_INT16 = 4
SIGNAL_DATA_UINT8 = 5
SIGNAL_DATA_INT8 = 6
SIGNAL_DATA_DOUBLE = 7
SIGNAL_DATA_TYPE_MAX = 8

SIGNAL_DATA_TYPE_FORMATS = {
    SIGNAL_DATA_FLOAT: ("<f", 4),
    SIGNAL_DATA_UINT32: ("<I", 4),
    SIGNAL_DATA_INT32: ("<i", 4),
    SIGNAL_DATA_UINT16: ("<H", 2),
    SIGNAL_DATA_INT16: ("<h", 2),
    SIGNAL_DATA_UINT8: ("<B", 1),
    SIGNAL_DATA_INT8: ("<b", 1),
    SIGNAL_DATA_DOUBLE: ("<d", 8),
}

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


def _cstr(value: bytes | bytearray) -> str:
    return bytes(value).split(b"\0", 1)[0].decode("latin1", "replace")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _list_infineon_dap_usb_devices() -> list[dict[str, str | int]]:
    devices = []
    for path in Path("/sys/bus/usb/devices").glob("*"):
        if _read_text(path / "idVendor").lower() != INFINEON_USB_VENDOR_ID:
            continue
        serial = _read_text(path / "serial")
        if not serial:
            continue
        product = _read_text(path / "product")
        busnum = int(_read_text(path / "busnum") or "0")
        devnum = int(_read_text(path / "devnum") or "0")
        devices.append(
            {
                "serial": serial,
                "product": product,
                "busnum": busnum,
                "devnum": devnum,
                "sys_name": path.name,
            }
        )
    return sorted(
        devices,
        key=lambda item: (int(item["busnum"]), int(item["devnum"]), str(item["serial"])),
    )


def _port_sel_from_dap_serial(serial: str) -> int | None:
    serial = serial.strip().lower()
    for index, device in enumerate(_list_infineon_dap_usb_devices()):
        if str(device["serial"]).lower() == serial:
            return index
    return None


def _format_dap_usb_devices() -> str:
    items = []
    for index, device in enumerate(_list_infineon_dap_usb_devices()):
        items.append(
            f"[{index}] serial={device['serial']} "
            f"bus={int(device['busnum']):03d} dev={int(device['devnum']):03d} "
            f"product={device['product']}"
        )
    return "; ".join(items) if items else "none"


def _parse_int(text: str) -> int:
    return int(text.replace("_", ""), 0)


def _parse_hex_bytes(text: str) -> bytes:
    raw = text.strip().replace(" ", "").replace("_", "")
    if raw.startswith("0x"):
        raw = raw[2:]
    if len(raw) % 2:
        raw = "0" + raw
    return bytes.fromhex(raw)


def _value_to_bytes(value: int | float | bytes | bytearray | str, byte_count: int) -> bytes:
    if isinstance(value, bytes):
        data = value
    elif isinstance(value, bytearray):
        data = bytes(value)
    elif isinstance(value, float):
        if byte_count == 4:
            data = struct.pack("<f", value)
        elif byte_count == 8:
            data = struct.pack("<d", value)
        else:
            raise ValueError(f"float value requires 4 or 8 byte(s), got {byte_count}")
    elif isinstance(value, str):
        int_value = _parse_int(value)
        data = int_value.to_bytes(byte_count, "little", signed=int_value < 0)
    else:
        int_value = int(value)
        data = int_value.to_bytes(byte_count, "little", signed=int_value < 0)
    if len(data) != byte_count:
        raise ValueError(f"value has {len(data)} byte(s), expected {byte_count}")
    return data


def _float_format_from_type(type_name: str, byte_count: int) -> str | None:
    normalized = type_name.strip().lower()
    if normalized == "double" and byte_count == 8:
        return "<d"
    if normalized == "float" and byte_count == 4:
        return "<f"
    return None


def _variable_value_from_bytes(data: bytes, info: VariableInfo) -> int | float:
    fmt = _float_format_from_type(info.type_name, len(data))
    if fmt:
        return struct.unpack(fmt, data)[0]
    return int.from_bytes(data, "little", signed=bool(info.signed))


def _variable_value_to_bytes(
    value: int | float | bytes | bytearray | str,
    info: VariableInfo,
    byte_count: int,
) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return _value_to_bytes(value, byte_count)

    fmt = _float_format_from_type(info.type_name, byte_count)
    if fmt:
        return struct.pack(fmt, float(value))
    return _value_to_bytes(value, byte_count)


def _load_elf_resolver() -> ctypes.CDLL:
    if not SO_PATH.exists():
        raise FileNotFoundError(f"C++ resolver not found: {SO_PATH}")
    lib = ctypes.CDLL(str(SO_PATH))
    lib.tc397_elf_write_member_index.argtypes = [
        c_char_p,
        c_char_p,
        ctypes.c_int,
        c_char_p,
        c_char_p,
        ctypes.c_size_t,
    ]
    lib.tc397_elf_write_member_index.restype = ctypes.c_int
    return lib


def generate_json_from_elf(elf_path: Path = ELF_PATH, json_path: Path = JSON_PATH, mcu_version: str | None = None) -> Path:
    if not elf_path.exists():
        raise FileNotFoundError(f"ELF file not found: {elf_path}")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    err = create_string_buffer(4096)
    rc = _load_elf_resolver().tc397_elf_write_member_index(
        str(elf_path).encode(),
        str(json_path).encode(),
        MAX_MEMBER_DEPTH,
        (mcu_version or "").encode(),
        err,
        len(err),
    )
    if rc != 0:
        raise RuntimeError(err.value.decode(errors="replace"))
    return json_path


def json_is_usable(json_path: Path = JSON_PATH) -> bool:
    if not json_path.exists():
        return False
    try:
        with json_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return False
    return (
        int(data.get("max_depth", -1)) == MAX_MEMBER_DEPTH
        and isinstance(data.get("entries_by_member"), dict)
    )


def get_json_mcu_version(json_path: Path = JSON_PATH) -> Optional[str]:
    if not json_path.exists():
        return None
    try:
        with json_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("mcu_version", None)


def ensure_json_index(elf_path: Path = ELF_PATH, json_path: Path = JSON_PATH, mcu_version: str | None = None) -> Path:
    if not elf_path.exists() and json_is_usable(json_path):
        return json_path
    JosnMcuversion = get_json_mcu_version(json_path)
    if JosnMcuversion != mcu_version:
        return generate_json_from_elf(elf_path, json_path,mcu_version)
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
        dap_serial: str | None = None,
    ) -> None:
        self.host = host
        self.server_index = server_index
        self.port_type = port_type
        self.port_sel = port_sel
        self.device_sel = device_sel
        self.addr_map = addr_map
        self.dap_serial = dap_serial
        self.port = None
        self.server_info: DasServerInfo | None = None

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
        server = self._select_server(servers)
        self._select_port_by_serial()
        if self.port_sel >= server.ports[self.port_type]:
            raise DasError(f"port select {self.port_sel} out of range")
        self.server_info = server

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

    def _select_server(self, servers: DasServersOnHostList) -> DasServerInfo:
        if self.server_index >= servers.n_das_servers:
            raise DasError(
                f"server index {self.server_index} out of range; "
                f"available servers: {self._format_servers(servers)}"
            )
        return servers.si[self.server_index]

    def _select_port_by_serial(self) -> None:
        if not self.dap_serial:
            return
        port_sel = _port_sel_from_dap_serial(self.dap_serial)
        if port_sel is None:
            raise DasError(
                f"DAP Wiggler serial '{self.dap_serial}' not found in USB devices; "
                f"available USB DAP devices: {_format_dap_usb_devices()}"
            )
        self.port_sel = port_sel

    @staticmethod
    def _format_servers(servers: DasServersOnHostList) -> str:
        items = []
        for index in range(servers.n_das_servers):
            server = servers.si[index]
            ports = [
                f"{port_type}:{count}"
                for port_type, count in enumerate(server.ports)
                if count
            ]
            items.append(
                f"[{index}] host={_cstr(server.server_host_addr)}:"
                f"{_cstr(server.server_host_port)} name={_cstr(server.name)} "
                f"manufacturer={_cstr(server.manufacturer_name)} "
                f"pid={server.process_id} ports={','.join(ports)}"
            )
        return "; ".join(items) if items else "none"

    def close(self) -> None:
        if self.port:
            error = c_uint(0)
            self._close_port(self.port, byref(error))
            self.port = None
            self.server_info = None

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

    def write(self, address: int, data: bytes) -> None:
        if not self.port:
            raise DasError("TAS is not connected")
        offset = 0
        while offset < len(data):
            chunk = data[offset : offset + DAS_MAX_TRANSFER_SIZE]
            buffer = create_string_buffer(chunk, len(chunk))
            self._execute_transaction(
                DAS_TRA_W | DAS_TRA_BYTE | DAS_TRA_RW_TRANSACTION,
                address + offset,
                buffer,
                len(chunk),
                "write",
            )
            offset += len(chunk)

    def _read_once(self, address: int, buffer, size: int) -> None:
        self._execute_transaction(
            DAS_TRA_R | DAS_TRA_BYTE | DAS_TRA_RW_TRANSACTION,
            address,
            buffer,
            size,
            "read",
        )

    def _execute_transaction(self, action: int, address: int, buffer, size: int, verb: str) -> None:
        tx = DasTransaction()
        tx.action = action
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
                f"{verb} failed: api={_describe_error(error.value)} "
                f"list=0x{tx_list.status:x} tx=0x{done.status:x} "
                f"tx_error={_describe_error(done.error)}"
            )

class ZPIMcuDownloader:
    """Download one MCU version from ZPI and keep only the final MCU*.elf."""

    _BASE_URL = "https://zpi.zeekrlife.com"
    _VERSION_ARCHIVE_LIST = "/ci/apigateway/pi-ci-build-report/rest/v1/versionArchive/list"
    _VERSION_ARCHIVE_DOWNLOAD = "/ci/apigateway/pi-ci-build-report/rest/v1/versionArchive/download"
    _CHUNK_SIZE = 1024 * 1024
    _ARCHIVE_SUFFIXES = (
        ".tar.gz",
        ".tar.bz2",
        ".tar.xz",
        ".tgz",
        ".tbz2",
        ".txz",
        ".zip",
        ".tar",
    )
    _DEFAULT_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        logger,
        username: str = None,
        password: str = None,
        dst_dir: str = None,
        # logger:
    ):
        self.logger = logger
        self.username = username or os.environ.get("ZPI_USERNAME", "ming.Kou")
        self.password = password or os.environ.get("ZPI_PASSWORD", "Zhu930215!!!")
        self.dst_dir = dst_dir or os.environ.get("ZPI_MCU_DST_DIR", "")
        self.session: requests.Session = None

    def download_mcu(self, mcu_version: str) -> str:
        """Download an MCU version and return the final MCU*.elf path."""
        if not mcu_version:
            raise RuntimeError("请传入 MCU 版本号")

        output_dir = self._output_dir(mcu_version)
        os.makedirs(output_dir, exist_ok=True)

        self.session = self._login()
        archive_url = self._get_mcu_archive_url(mcu_version)
        archive_path = os.path.join(output_dir, self._archive_name(archive_url))

        self.logger.info("下载 MCU 压缩包: %s", os.path.basename(archive_path))
        self._download_file(archive_url, archive_path)

        extract_dir = os.path.join(output_dir, "_extract")
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)
        self._extract_recursive(archive_path, extract_dir)

        final_path = self._keep_only_target_elf(output_dir, self._find_unique_mcu_elf(extract_dir))
        self.logger.info("MCU ELF 已完成: %s", final_path)
        return final_path

    def _login(self) -> requests.Session:
        if not self.username or not self.password:
            raise RuntimeError("缺少 ZPI_USERNAME/ZPI_PASSWORD")

        session = requests.Session()
        session.headers.update({"User-Agent": self._DEFAULT_UA})

        page = session.get(f"{self._BASE_URL}/api/pi-sso/login", verify=False, timeout=30)
        page.raise_for_status()
        match = re.search(r"let secretKey = `(.*?)`", page.text)
        if not match:
            raise RuntimeError("未在 ZPI 登录页找到 secretKey")

        encrypted_pwd = self._encrypt_password(self.password, match.group(1))
        last_data = None
        for attempt in range(1, 4):
            resp = session.post(
                f"{self._BASE_URL}/api/pi-platform-auth/rest/v1/login/loginToken",
                json={"userName": self.username, "userPwd": encrypted_pwd, "isGh": False},
                verify=False,
                timeout=30,
            )
            resp.raise_for_status()
            last_data = resp.json()
            if last_data.get("code") == 200:
                token = last_data["data"]["token"]
                session.get(f"{self._BASE_URL}/home?token={token}", verify=False, timeout=30)
                self.logger.info("ZPI 登录成功")
                return session
            time.sleep(attempt)
        raise RuntimeError(f"ZPI 登录失败: {last_data}")

    def _get_mcu_archive_url(self, mcu_version: str) -> str:
        payload = {"archiveVersion": mcu_version, "pageQuery": {"current": 1, "size": 20}}
        data = self._post_json(self._VERSION_ARCHIVE_LIST, payload)
        archives = self._flatten_mcu_archives(data, mcu_version)
        if not archives:
            raise RuntimeError(f"未找到 MCU 版本 {mcu_version} 的归档压缩包")

        archive = self._select_mcu_archive(archives, mcu_version)
        archive_id = archive.get("id")
        if not archive_id:
            raise RuntimeError(f"MCU 归档缺少 id: {archive}")

        download = self._get_json(f"{self._VERSION_ARCHIVE_DOWNLOAD}/{archive_id}")
        if download.get("code") != 0 or not download.get("data"):
            raise RuntimeError(f"获取 MCU 下载链接失败: {download}")
        return download["data"]

    def _post_json(self, path: str, payload: dict) -> dict:
        resp = self.session.post(
            f"{self._BASE_URL}{path}",
            data=json.dumps(payload),
            headers={"content-type": "application/json;charset=UTF-8"},
            verify=False,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def _get_json(self, path: str) -> dict:
        resp = self.session.get(f"{self._BASE_URL}{path}", verify=False, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _flatten_mcu_archives(self, response: dict, mcu_version: str) -> list[dict]:
        archives = []
        for record in self._iter_dicts(response):
            name = str(record.get("name") or "")
            if (
                record.get("archiveVersion") == mcu_version
                and record.get("id")
                and self._is_archive(name)
            ):
                archives.append(record)
        return self._unique_records(archives)

    def _select_mcu_archive(self, archives: list[dict], mcu_version: str) -> dict:
        base_version = mcu_version.rsplit(".", 1)[0]

        def score(item: dict) -> tuple[int, int, int]:
            name = str(item.get("name") or "")
            return (
                int(name.startswith(base_version)),
                int(not name.upper().startswith("MCUPERFCFG")),
                self._parse_size_bytes(item.get("size")),
            )

        candidates = [item for item in archives if item.get("archive") == "mcu"] or archives
        return max(candidates, key=score)

    def _download_file(self, url: str, save_path: str, retries: int = 3) -> None:
        last_exc: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            tmp_path = save_path + ".part"
            try:
                with requests.get(url, stream=True, verify=False, timeout=120) as resp:
                    resp.raise_for_status()
                    expected = int(resp.headers.get("Content-Length") or 0)
                    written = 0
                    with open(tmp_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=self._CHUNK_SIZE):
                            if chunk:
                                f.write(chunk)
                                written += len(chunk)
                    if expected and written != expected:
                        raise IOError(f"下载字节数不一致: expected={expected}, actual={written}")
                os.replace(tmp_path, save_path)
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                if attempt < retries:
                    time.sleep(2**attempt)
        raise RuntimeError(f"下载失败: {save_path}") from last_exc

    def _extract_recursive(self, initial_archive: str, extract_dir: str) -> None:
        os.makedirs(extract_dir, exist_ok=True)
        pending = [os.path.abspath(initial_archive)]
        processed = set()
        while pending:
            archive = pending.pop(0)
            real_path = os.path.realpath(archive)
            if real_path in processed:
                continue
            processed.add(real_path)

            target_dir = os.path.join(extract_dir, os.path.basename(archive) + "_extract")
            self._extract_one(archive, target_dir)
            for root, _, files in os.walk(target_dir):
                for filename in files:
                    path = os.path.join(root, filename)
                    if self._is_archive(path):
                        pending.append(path)

    def _extract_one(self, archive: str, dst_dir: str) -> None:
        os.makedirs(dst_dir, exist_ok=True)
        if archive.lower().endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                self._check_zip_safe(zf, dst_dir)
                zf.extractall(dst_dir)
            return
        if self._is_archive(archive):
            with tarfile.open(archive) as tf:
                self._check_tar_safe(tf, dst_dir)
                tf.extractall(dst_dir)
            return
        raise RuntimeError(f"不支持的压缩包格式: {archive}")

    def _find_unique_mcu_elf(self, root_dir: str) -> str:
        matches = []
        for root, _, files in os.walk(root_dir):
            for filename in files:
                lower = filename.lower()
                if lower.startswith("mcu") and lower.endswith(".elf"):
                    matches.append(os.path.join(root, filename))
        if not matches:
            raise RuntimeError(f"未找到 MCU*.elf: {root_dir}")
        if len(matches) > 1:
            raise RuntimeError("找到多个 MCU*.elf: " + ", ".join(matches))
        return matches[0]

    def _keep_only_target_elf(self, output_dir: str, target_elf: str) -> str:
        output_abs = os.path.abspath(output_dir)
        target_abs = os.path.abspath(target_elf)
        if not self._is_child(target_abs, output_abs):
            raise RuntimeError(f"目标 ELF 不在输出目录内: {target_elf}")

        final_path = os.path.join(output_abs, os.path.basename(target_abs))
        keep_path = final_path + ".keep"
        shutil.move(target_abs, keep_path)
        for name in os.listdir(output_abs):
            path = os.path.join(output_abs, name)
            if os.path.abspath(path) == os.path.abspath(keep_path):
                continue
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        os.replace(keep_path, final_path)
        return final_path

    def _encrypt_password(self, password: str, public_key_body: str) -> str:
        pem = (
            "-----BEGIN PUBLIC KEY-----\n"
            + public_key_body.strip()
            + "\n-----END PUBLIC KEY-----"
        ).encode("utf-8")
        password_bytes = password.encode("utf-8")
        try:
            from Crypto.Cipher import PKCS1_v1_5 as Cipher_pkcs1_v1_5
            from Crypto.PublicKey import RSA

            encrypted = Cipher_pkcs1_v1_5.new(RSA.importKey(pem)).encrypt(password_bytes)
        except ModuleNotFoundError:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import padding

            public_key = serialization.load_pem_public_key(pem)
            encrypted = public_key.encrypt(password_bytes, padding.PKCS1v15())
        return base64.b64encode(encrypted).decode("utf-8")

    def _iter_dicts(self, node) -> Iterable[dict]:
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from self._iter_dicts(value)
        elif isinstance(node, list):
            for item in node:
                yield from self._iter_dicts(item)

    def _unique_records(self, records: list[dict]) -> list[dict]:
        seen = set()
        unique = []
        for record in records:
            key = json.dumps(record, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                seen.add(key)
                unique.append(record)
        return unique

    def _check_zip_safe(self, zf: zipfile.ZipFile, dst_dir: str) -> None:
        root = os.path.abspath(dst_dir)
        for member in zf.infolist():
            target = os.path.abspath(os.path.join(dst_dir, member.filename))
            self._ensure_inside(root, target, member.filename)

    def _check_tar_safe(self, tf: tarfile.TarFile, dst_dir: str) -> None:
        root = os.path.abspath(dst_dir)
        for member in tf.getmembers():
            target = os.path.abspath(os.path.join(dst_dir, member.name))
            self._ensure_inside(root, target, member.name)

    def _ensure_inside(self, root: str, target: str, display: str) -> None:
        if not (target == root or target.startswith(root + os.sep)):
            raise RuntimeError(f"压缩包包含非法路径: {display}")

    def _parse_size_bytes(self, value) -> int:
        if isinstance(value, (int, float)):
            return int(value)
        if not isinstance(value, str):
            return 0
        match = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([kmgt]?b)?\s*$", value, flags=re.I)
        if not match:
            return 0
        number = float(match.group(1))
        unit = (match.group(2) or "b").lower()
        multipliers = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}
        return int(number * multipliers.get(unit, 1))

    def _archive_name(self, url: str) -> str:
        name = os.path.basename(unquote(urlparse(url).path).rstrip("/\\"))
        return name or "mcu_package.tar.gz"

    def _is_archive(self, path: str) -> bool:
        return path.lower().endswith(self._ARCHIVE_SUFFIXES)

    def _is_child(self, child: str, parent: str) -> bool:
        child = os.path.abspath(child)
        parent = os.path.abspath(parent)
        return child == parent or child.startswith(parent + os.sep)

    def _safe_name(self, value: str) -> str:
        return re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._") or "mcu_version"

    def _output_dir(self, mcu_version: str) -> str:
        if self.dst_dir:
            return os.path.abspath(self.dst_dir)
        return os.path.abspath(os.path.join("zpi_download", "mcu", self._safe_name(mcu_version)))


class BaseTas:
    def __init__(
        self,
        logger,
        elf_path: Path = ELF_PATH,
        json_path: Path = JSON_PATH,
        das_home: str | Path = os.environ.get("DAS_HOME", DEFAULT_DAS_HOME),
        dap_serial: str | None = None,
        mcu_version: str | None = None,
    ) -> None:
        self.elf_path = elf_path
        self.json_path = json_path
        self.das_home = str(das_home)
        self.index: VariableIndex | None = None
        self.client: DasClient | None = None
        self.tas_process: subprocess.Popen | None = None
        self.dap_serial = dap_serial
        self.is_connected = False
        self.zipHelper = ZPIMcuDownloader(logger=logger, dst_dir=self.elf_path.parent)
        self.mcu_version = mcu_version
        

    def prepare_index(self) -> VariableIndex:
        oldMcuVersion = get_json_mcu_version(self.json_path)
        if oldMcuVersion is None or  self.mcu_version != oldMcuVersion:
            self.download_mcu()
        ensure_json_index(self.elf_path, self.json_path,self.mcu_version)
        self.index = VariableIndex(self.json_path)
        return self.index

    def download_mcu(self) -> Path:
        self.zipHelper.download_mcu(self.mcu_version)
    
    def connect(self) -> DasDeviceInfo:
        self._ensure_tas_server()
        self.disconnect()
        client = DasClient(
            das_home=self.das_home,
            host="127.0.0.1",
            server_index=0,
            port_type=DAS_PT_JTAG,
            port_sel=0,
            device_sel=0,
            addr_map=DAS_AMAP_DEVICE_MIN,
            dap_serial=self.dap_serial,
        )
        try:
            device_info = client.open()
        except Exception:
            client.close()
            self.client = None
            self.is_connected = False
            raise
        self.client = client
        self.is_connected = True
        return device_info

    def disconnect(self) -> None:
        if self.is_connected and self.client:
            self.client.close()
        self.client = None
        self.is_connected = False

    def resolve_variable(self, name: str) -> VariableInfo:
        if self.index is None:
            self.prepare_index()
        assert self.index is not None
        return self.index.resolve(name)

    def read_variable(self, name: str, byte_count: int | None = None) -> bytes:
        info = self.resolve_variable(name)
        return self.read_variable_info(info, byte_count)

    def read_variable_info(self, info: VariableInfo, byte_count: int | None = None) -> bytes:
        if not self.client:
            raise DasError("TAS is not connected; call connect() first")
        size = byte_count or info.byte_size
        if size <= 0:
            raise ValueError(f"unknown byte size for {info.name}; pass byte_count")
        return self.client.read(info.address, size)

    def read_variable_value(self, name: str, byte_count: int | None = None) -> int | float:
        info = self.resolve_variable(name)
        data = self.read_variable_info(info, byte_count)
        return _variable_value_from_bytes(data, info)

    def read_variable_info_value(self, info: VariableInfo, byte_count: int | None = None) -> int | float:
        data = self.read_variable_info(info, byte_count)
        return _variable_value_from_bytes(data, info)

    def parse_signal_id_value_by_data(self, data: bytes | bytearray | memoryview) -> dict:
        """
        解析按 sigID/QF/type/value 连续排列的信号数据。
        布局: sigID(2B) + QF(1B) + AdapterSignaDataTypeEnum(1B) + value(NB)。
        """
        raw = bytes(data)
        offset = 0
        result = {}
        header_size = 4

        while offset < len(raw):
            remain = len(raw) - offset
            if remain < header_size:
                break

            sig_id = int.from_bytes(raw[offset : offset + 2], "little", signed=False)
            if sig_id == 0:
                break

            qf = raw[offset + 2]
            data_type = raw[offset + 3]
            offset += header_size

            if data_type == SIGNAL_DATA_TYPE_MAX or data_type not in SIGNAL_DATA_TYPE_FORMATS:
                break

            fmt, value_size = SIGNAL_DATA_TYPE_FORMATS[data_type]
            if len(raw) - offset < value_size:
                break

            value = struct.unpack_from(fmt, raw, offset)[0]
            offset += value_size
            result[sig_id] = {"QF": qf, "value": value}

        return result

    def parse_signal_id_value_by(self, data: bytes | bytearray | memoryview) -> dict:
        return self.parse_signal_id_value_by_data(data)

    def write_variable(
        self,
        name: str,
        value: int | float | bytes | bytearray | str,
        byte_count: int | None = None,
    ) -> None:
        info = self.resolve_variable(name)
        self.write_variable_info(info, value, byte_count)

    def write_variable_info(
        self,
        info: VariableInfo,
        value: int | float | bytes | bytearray | str,
        byte_count: int | None = None,
    ) -> None:
        if not self.client:
            raise DasError("TAS is not connected; call connect() first")
        size = byte_count or info.byte_size
        if size <= 0:
            raise ValueError(f"unknown byte size for {info.name}; pass byte_count")
        self.client.write(info.address, _variable_value_to_bytes(value, info, size))

    def write_variable_bytes(self, name: str, hex_bytes: str, byte_count: int | None = None) -> None:
        if not self.client:
            raise DasError("TAS is not connected; call connect() first")
        info = self.resolve_variable(name)
        data = _parse_hex_bytes(hex_bytes)
        size = byte_count or len(data) or info.byte_size
        if len(data) != size:
            raise ValueError(f"value has {len(data)} byte(s), expected {size}")
        self.client.write(info.address, data)

    def write_variable_info_bytes(
        self,
        info: VariableInfo,
        hex_bytes: str,
        byte_count: int | None = None,
    ) -> None:
        if not self.client:
            raise DasError("TAS is not connected; call connect() first")
        data = _parse_hex_bytes(hex_bytes)
        size = byte_count or len(data) or info.byte_size
        if len(data) != size:
            raise ValueError(f"value has {len(data)} byte(s), expected {size}")
        self.client.write(info.address, data)

    def _ensure_tas_server(self) -> None:
        probe = DasClient(
            das_home=self.das_home,
            host="127.0.0.1",
            server_index=0,
            port_type=DAS_PT_JTAG,
            port_sel=0,
            device_sel=0,
            addr_map=DAS_AMAP_DEVICE_MIN,
            dap_serial=self.dap_serial,
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
    import logging
    logger = logging.getLogger("tas_example")
    tas = BaseTas(dap_serial="AK9D8A6W",mcu_version="ZRD.MCU.VG30TU.0.1.2025.7.1.2318330602041305",logger=logger)
    data = [0x02,0x00,0x23,0x03,0x00,0x00,0x03,0x00,0x23,0x05,0x00,0x04,0x00,0x23,0x05,0x00,0x05,0x00,0x03,0x05,0x00,0x06,0x00,0x03,0x05,0x00,0x07,0x00,0x03,0x05,0x00,0x08,0x00,0x03,0x05,0x00,0x09,0x00,0x23,0x05,0x00,0x2c,0x00,0x21,0x00,0x00,0x00,0x00,0x00,0x31,0x00,0x21,0x00,0x00,0x00,0x00,0x00,0x34,0x00,0x20,0x00,0x00,0x00,0x00,0x00,0x37,0x00,0x20,0x00,0x00,0x00,0x00,0x00,0x38,0x00,0x23,0x05,0x00,0x3f,0x00,0x21,0x05,0x00,0x40,0x00,0x23,0x05,0x00,0x51,0x00,0x23,0x05,0x00,0x52,0x00,0x23,0x05,0x00,0x53,0x00,0x23,0x05,0x00,0x54,0x00,0x23,0x05,0x00,0x55,0x00,0x23,0x05,0x00,0x56,0x00,0x23,0x05,0x00,0x57,0x00,0x23,0x05,0x00,0x58,0x00,0x23,0x05,0x00,0x59,0x00,0x03,0x00,0x00,0x00,0x00,0x00,0x5a,0x00,0x23,0x05,0x01,0x5b,0x00,0x23,0x00,0x00,0x00,0x00,0x00,0x5c,0x00,0x00,0x05,0x00,0x5d,0x00,0x23,0x05,0x00,0x5e,0x00,0x03,0x05,0x00,0x5f,0x00,0x23,0x00,0x00,0x00,0x00,0x00,0x60,0x00,0x20,0x05,0x00,0x61,0x00,0x20,0x05,0x00,0x62,0x00,0x20,0x05,0x00,0x63,0x00,0x20,0x00,0x00,0x00,0x00,0x00,0x64,0x00,0x23,0x05,0x00,0x65,0x00,0x21,0x00,0x00,0x00,0x00,0x00,0x66,0x00,0x00,0x05,0x00,0x67,0x00,0x00,0x05,0x00,0x68,0x00,0x00,0x05,0x00,0x69,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x6a,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x6b,0x00,0x23,0x03,0x00,0x00,0x6c,0x00,0x23,0x05,0x00,0x6d,0x00,0x23,0x05,0x00,0x6e,0x00,0x23,0x05,0x00,0x6f,0x00,0x23,0x05,0x00,0x70,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x71,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x72,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x73,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x74,0x00,0x03,0x05,0x00,0x75,0x00,0x03,0x05,0x00,0x76,0x00,0x03,0x05,0x00,0x77,0x00,0x03,0x05,0x00,0x78,0x00,0x23,0x05,0x00,0x79,0x00,0x23,0x05,0x00,0x7a,0x00,0x23,0x05,0x00,0x7b,0x00,0x23,0x05,0x00,0x7c,0x00,0x03,0x05,0x00,0x7d,0x00,0x03,0x05,0x00,0x7e,0x00,0x03,0x05,0x00,0x7f,0x00,0x03,0x05,0x00,0x80,0x00,0x03,0x05,0x00,0x81,0x00,0x03,0x05,0x00,0x82,0x00,0x03,0x05,0x00,0x83,0x00,0x03,0x05,0x00,0x84,0x00,0x23,0x00,0xb8,0x0e,0xaf,0x43,0x85,0x00,0x23,0x00,0xb8,0x0e,0xaf,0x43,0x86,0x00,0x23,0x00,0xb8,0x0e,0xaf,0x43,0x87,0x00,0x23,0x00,0xb8,0x0e,0xaf,0x43,0x88,0x00,0x23,0x05,0x00,0x89,0x00,0x23,0x05,0x00,0x8a,0x00,0x23,0x05,0x00,0x8b,0x00,0x23,0x05,0x00,0x8c,0x00,0x23,0x05,0x00,0x8d,0x00,0x23,0x05,0x00,0x8e,0x00,0x23,0x05,0x00,0x8f,0x00,0x21,0x00,0x00,0x00,0x00,0x00,0x90,0x00,0x21,0x00,0x00,0x00,0x00,0x00,0x91,0x00,0x21,0x05,0x00,0x92,0x00,0x23,0x05,0x00,0x94,0x00,0x23,0x05,0x01,0x95,0x00,0x23,0x05,0x01,0x96,0x00,0x23,0x05,0x00,0x97,0x00,0x23,0x05,0x00,0x98,0x00,0x23,0x05,0x00,0x99,0x00,0x23,0x05,0x00,0x9a,0x00,0x23,0x05,0x00,0x9b,0x00,0x23,0x05,0x00,0x9c,0x00,0x23,0x00,0x00,0x00,0x00,0x00,0x9d,0x00,0x20,0x00,0x00,0x00,0x00,0x00,0x9e,0x00,0x20,0x00,0x00,0x00,0x00,0x00,0x9f,0x00,0x20,0x00,0x00,0x00,0x00,0x00,0xa0,0x00,0x20,0x00,0x00,0x00,0x00,0x00,0xa1,0x00,0x23,0x05,0x00,0xa2,0x00,0x21,0x00,0x00,0x00,0x00,0x00,0xa3,0x00,0x23,0x05,0x00,0xa4,0x00,0x23,0x05,0x00,0xa5,0x00,0x23,0x05,0x00,0xa6,0x00,0x23,0x05,0x00,0xa7,0x00,0x23,0x00,0x00,0x00,0x00,0x00,0xa8,0x00,0x23,0x00,0x00,0x00,0x00,0x00,0xa9,0x00,0x21,0x05,0x00,0xaa,0x00,0x23,0x05,0x00,0xab,0x00,0x03,0x05,0x00,0xac,0x00,0x03,0x05,0x00,0xad,0x00,0x23,0x00,0x00,0x00,0x00,0x00,0xae,0x00,0x23,0x00,0x00,0x00,0x00,0x00,0xaf,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xb3,0x00,0x00,0x05,0x00,0xb4,0x00,0x03,0x05,0x00,0xbc,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xbd,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xbe,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xc8,0x00,0x23,0x05,0x00,0xcb,0x00,0x03,0x05,0x00,0xcc,0x00,0x03,0x05,0x00,0xce,0x00,0x23,0x05,0x00,0xcf,0x00,0x03,0x05,0x00,0xd0,0x00,0x03,0x05,0x00,0xd1,0x00,0x23,0x05,0x00,0xd2,0x00,0x23,0x05,0x00,0xd3,0x00,0x23,0x05,0x00,0xd5,0x00,0x23,0x05,0x00,0xd6,0x00,0x23,0x05,0x00,0xd7,0x00,0x23,0x05,0x00,0xd8,0x00,0x23,0x05,0x00,0xd9,0x00,0x23,0x05,0x00,0xda,0x00,0x23,0x05,0x00,0xdb,0x00,0x23,0x05,0x00,0xdc,0x00,0x23,0x05,0x00,0xdd,0x00,0x20,0x05,0x00,0xde,0x00,0x23,0x05,0x00,0xe0,0x00,0x23,0x05,0x00,0xe1,0x00,0x23,0x05,0x00,0xe2,0x00,0x23,0x05,0x00,0xe3,0x00,0x23,0x05,0x00,0xe4,0x00,0x23,0x05,0x00,0xe5,0x00,0x23,0x05,0x00,0xe6,0x00,0x03,0x05,0x00,0xe7,0x00,0x03,0x00,0x00,0x00,0x00,0x00,0xe8,0x00,0x03,0x05,0x07,0xe9,0x00,0x23,0x05,0x00,0xea,0x00,0x03,0x05,0x00,0xeb,0x00,0x23,0x05,0x00,0xec,0x00,0x00,0x05,0x00,0xed,0x00,0x03,0x00,0x00,0x00,0x00,0x00,0xee,0x00,0x20,0x05,0x00,0xef,0x00,0x23,0x00,0x00,0x00,0x00,0x00,0xf0,0x00,0x03,0x05,0x00,0xf1,0x00,0x03,0x05,0x00,0xf2,0x00,0x03,0x05,0x00,0xf3,0x00,0x03,0x05,0x00,0xf4,0x00,0x03,0x05,0x00,0xf5,0x00,0x03,0x05,0x00,0xf7,0x00,0x01,0x00,0x00,0x00,0x00,0x00,0xf8,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xfb,0x00,0x23,0x00,0x00,0x00,0x00,0x00,0xfc,0x00,0x23,0x00,0x00,0x00,0x00,0x00,0xfd,0x00,0x23,0x00,0x00,0x00,0x00,0x00,0xfe,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xff,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x01,0x01,0x23,0x05,0x00,0x02,0x01,0x21,0x05,0x00,0x03,0x01,0x23,0x05,0x00,0x04,0x01,0x23,0x05,0x00,0x05,0x01,0x23,0x05,0x00,0x07,0x01,0x23,0x05,0x01,0x09,0x01,0x23,0x05,0x00,0x0d,0x01,0x23,0x05,0x00,0x0e,0x01,0x23,0x05,0x00,0x0f,0x01,0x23,0x05,0x00,0x10,0x01,0x23,0x05,0x00,0x11,0x01,0x21,0x03,0x00,0x00,0x12,0x01,0x23,0x05,0x00,0x13,0x01,0x23,0x05,0x00,0x14,0x01,0x23,0x05,0x03,0x15,0x01,0x00,0x00,0x00,0x00,0x00,0x00,0x16,0x01,0x23,0x05,0x00,0x17,0x01,0x21,0x00,0x00,0x00,0x00,0x00,0x18,0x01,0x23,0x03,0x00,0x00,0x19,0x01,0x23,0x03,0x00,0x00,0x1a,0x01,0x23,0x03,0x00,0x00,0x1b,0x01,0x23,0x03,0x00,0x00,0x1d,0x01,0x23,0x05,0x00,0x1e,0x01,0x20,0x05,0x00,0x1f,0x01,0x23,0x05,0x01,0x20,0x01,0x23,0x05,0x00,0x21,0x01,0x23,0x05,0x00,0x22,0x01,0x23,0x05,0x00,0x23,0x01,0x23,0x05,0x00,0x24,0x01,0x23,0x05,0x00,0x25,0x01,0x23,0x05,0x00,0x26,0x01,0x23,0x05,0x00,0x27,0x01,0x23,0x05,0x00,0x28,0x01,0x23,0x05,0x02,0x29,0x01,0x23,0x05,0x00,0x2a,0x01,0x21,0x00,0x00,0x00,0x00,0x00,0x2c,0x01,0x03,0x05,0x00,0x2d,0x01,0x23,0x05,0x00,0x2e,0x01,0x23,0x05,0x0e,0x2f,0x01,0x23,0x05,0x05,0x30,0x01,0x21,0x00,0x00,0x00,0x00,0x00,0x31,0x01,0x03,0x05,0x01,0x32,0x01,0x23,0x05,0x00,0x34,0x01,0x01,0x00,0x00,0x00,0x00,0x00,0x35,0x01,0x03,0x05,0x00,0x36,0x01,0x03,0x05,0x00,0x37,0x01,0x01,0x00,0x00,0x00,0x00,0x00,0x38,0x01,0x03,0x05,0x00,0x39,0x01,0x03,0x05,0x00,0x3a,0x01,0x03,0x05,0x00,0x3b,0x01,0x03,0x05,0x00,0x3c,0x01,0x03,0x05,0x00,0x3d,0x01,0x03,0x05,0x00,0x3e,0x01,0x03,0x05,0x00,0x3f,0x01,0x03,0x05,0x00,0x41,0x01,0x20,0x00,0x00,0x00,0x00,0x00,0x42,0x01,0x03,0x05,0x00,0x43,0x01,0x23,0x05,0x00,0x44,0x01,0x23,0x00,0x00,0x00,0x00,0x00,0x46,0x01,0x23,0x05,0x01,0x47,0x01,0x23,0x05,0x08,0x49,0x01,0x03,0x05,0x02,0x4a,0x01,0x03,0x05,0x00,0x4b,0x01,0x23,0x05,0x00,0x4c,0x01,0x23,0x05,0x00,0x4d,0x01,0x23,0x05,0x00,0x4e,0x01,0x23,0x00,0x00,0x00,0x00,0x00,0x50,0x01,0x23,0x05,0x00,0x52,0x01,0x03,0x05,0x00,0x54,0x01,0x03,0x05,0x00,0x56,0x01,0x03,0x05,0x00,0x58,0x01,0x03,0x05,0x00,0x59,0x01,0x20,0x05,0x00,0x5a,0x01,0x20,0x05,0x02,0x5b,0x01,0x20,0x05,0x00,0x5d,0x01,0x20,0x05,0x00,0x5e,0x01,0x20,0x05,0x00,0x5f,0x01,0x20,0x05,0x00,0x61,0x01,0x20,0x05,0x00,0x62,0x01,0x23,0x04,0x00,0x00,0x63,0x01,0x23,0x04,0x00,0x00,0x64,0x01,0x23,0x04,0x00,0x00,0x65,0x01,0x20,0x00,0x00,0x00,0x00,0x00,0x66,0x01,0x23,0x05,0x00,0x67,0x01,0x23,0x04,0x00,0x00,0x68,0x01,0x20,0x00,0x00,0x00,0x00,0x00,0x69,0x01,0x23,0x05,0x00,0x6a,0x01,0x23,0x05,0x00,0x6b,0x01,0x23,0x05,0x00,0x6c,0x01,0x23,0x05,0x00,0x6d,0x01,0x23,0x05,0x00,0x6e,0x01,0x23,0x05,0x00,0x6f,0x01,0x23,0x05,0x00,0x70,0x01,0x23,0x05,0x00,0x71,0x01,0x23,0x05,0x02,0x74,0x01,0x23,0x04,0x00,0x00,0x75,0x01,0x23,0x04,0x00,0x00,0x76,0x01,0x23,0x05,0x00,0x77,0x01,0x23,0x00,0x00,0x00,0x00,0x00,0x78,0x01,0x23,0x05,0x00,0x79,0x01,0x03,0x05,0x00,0x7a,0x01,0x23,0x05,0x00,0x7d,0x01,0x23,0x05,0x00,0x7e,0x01,0x23,0x05,0x00,0x80,0x01,0x23,0x05,0x01,0x81,0x01,0x23,0x05,0x00,0x82,0x01,0x23,0x00,0x00,0x00,0x00,0x00,0x88,0x01,0x23,0x05,0x00,0x8a,0x01,0x03,0x05,0x00,0x8b,0x01,0x23,0x05,0x00,0x8c,0x01,0x23,0x03,0x00,0x00,0x8d,0x01,0x23,0x05,0x00,0x8e,0x01,0x23,0x05,0x00,0x92,0x01,0x23,0x05,0x00,0x93,0x01,0x23,0x05,0x00,0x94,0x01,0x01,0x00,0x00,0x00,0x00,0x00,0x95,0x01,0x23,0x05,0x00,0x96,0x01,0x23,0x05,0x00,0x97,0x01,0x23,0x05,0x00,0x98,0x01,0x23,0x05,0x00,0x99,0x01,0x23,0x05,0x00,0x9a,0x01,0x23,0x05,0x00,0x9b,0x01,0x23,0x05,0x00,0x9c,0x01,0x23,0x05,0x00,0x9d,0x01,0x23,0x05,0x00,0x9e,0x01,0x23,0x00,0x00,0x00,0x00,0x00,0x9f,0x01,0x23,0x04,0x00,0x00,0xa0,0x01,0x23,0x04,0x00,0x00,0xa1,0x01,0x23,0x04,0x00,0x00,0xa2,0x01,0x23,0x04,0x00,0x00,0xa3,0x01,0x23,0x04,0x00,0x00,0xa4,0x01,0x23,0x04,0x00,0x00,0xa5,0x01,0x23,0x05,0x01,0xa6,0x01,0x23,0x05,0x01,0xa7,0x01,0x23,0x05,0x01,0xa8,0x01,0x23,0x05,0x00,0xa9,0x01,0x23,0x05,0x00,0xaa,0x01,0x23,0x05,0x00,0xab,0x01,0x23,0x05,0x00,0xac,0x01,0x23,0x05,0x00,0xb4,0x01,0x23,0x05,0x00,0xb5,0x01,0x23,0x00,0x00,0x00,0x00,0x00,0xb6,0x01,0x23,0x05,0x00,0xb7,0x01,0x23,0x05,0x00,0xb8,0x01,0x23,0x05,0x00,0xb9,0x01,0x23,0x05,0x00,0xbb,0x01,0x23,0x05,0x00,0xbc,0x01,0x23,0x05,0x00,0xbe,0x01,0x23,0x05,0x02,0xc5,0x01,0x23,0x00,0x00,0x00,0x00,0x00,0xc6,0x01,0x23,0x05,0x00,0xc7,0x01,0x23,0x05,0x00,0xe1,0x01,0x23,0x05,0x00,0xe2,0x01,0x23,0x05,0x00,0xe3,0x01,0x23,0x05,0x01,0xe7,0x01,0x23,0x05,0x00,0xfe,0x01,0x23,0x05,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00]
    data = bytes(data)
    SignalData = tas.parse_signal_id_value_by_data(data)
    print(SignalData)
    info = tas.resolve_variable("VehModMngtGlbSafe1UsgModSts")
    print(f"name={info.name} address=0x{info.address:08x} size={info.byte_size} type={info.type_name}")
    try:
        tas.connect()
        data = tas.read_variable("VehModMngtGlbSafe1UsgModSts", None)
        value = int.from_bytes(data, "little", signed=bool(info.signed))
        print(f"bytes={data.hex()} value={value} hex=0x{value:x}")
        tas.write_variable("VehModMngtGlbSafe1UsgModSts", value + 1)
        time.sleep(0.1)
        data = tas.read_variable("VehModMngtGlbSafe1UsgModSts", None)
        value = int.from_bytes(data, "little", signed=bool(info.signed))
        print(f"bytes={data.hex()} value={value} hex=0x{value:x}")
    finally:
        tas.disconnect()
