#!/usr/bin/env python3
"""Read/write TC397 memory through Infineon DAS/TAS using ELF symbol names.

Run inside the prepared virtualenv:
    source ~/.local/bin/virtualenvwrapper.sh
    workon test_env
    python scripts/tc397_tas_var.py --help

This helper does not use winIDEA. It talks to the Linux TAS server through the
Infineon DAS client library and uses pyelftools only to resolve variable names
to ELF addresses and sizes.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from elftools.dwarf.descriptions import describe_form_class
from elftools.dwarf.dwarf_expr import DWARFExprParser
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection


DEFAULT_DAS_HOME = "/opt/Tools/DAS/8.3.0"
DEFAULT_SIGNAL_ALIAS_PATH = None
DAS_MAX_PORT_TYPES = 64
DAS_MAX_SERVERS_PER_HOST = 16
DAS_MAX_TRANSFER_SIZE = 1024

DAS_PT_JTAG = 3
DAS_MPO_DEFAULT = 0
DAS_MPO_CTDO_NO_RST = 0x00000001
DAS_DIO_HOT_ATTACH = 0x00000000
DAS_DIO_RESET_AND_HALT = 0x00000006
DAS_AMAP_DEVICE_MIN = 0

DAS_TRA_R = 0x00
DAS_TRA_W = 0x01
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
    0x00000080: "ADDR_RANGE_RESERVED",
    0x00000100: "PORT_ACCESS",
    0x00000200: "DASHPAS",
    0x00001000: "SERVER_LOCKED",
    0x00004000: "SERVER_ECF",
    0x00010000: "TIMEOUT",
    0x00020000: "LIST_RULE",
    0x00080000: "COMMAND_FAILED",
    0x01000000: "PARAMETER",
    0x02000000: "CONNECTION",
    0x04000000: "LIST_SEQUENCE",
    0x08000000: "NO_SERVER",
    0x10000000: "SERVER_CORE",
    0x80000000: "FATAL",
}

VARIABLE_REF_PATTERN = re.compile(r"^(?P<name>.+?)(?:\[(?P<index>0x[0-9a-fA-F]+|\d+)\])?$")


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
class ElfVariable:
    name: str
    address: int
    size: int
    signed: bool | None = None
    source: str = "symtab"
    symbol_type: str = ""
    binding: str = ""
    visibility: str = ""
    section_index: str = ""
    section_name: str = ""
    type_name: str = ""


@dataclass(frozen=True)
class ElfVariableReference:
    expression: str
    variable: ElfVariable
    byte_offset: int = 0
    indexed: bool = False
    byte_count_override: int | None = None

    @property
    def address(self) -> int:
        return self.variable.address + self.byte_offset

    @property
    def default_byte_count(self) -> int:
        if self.byte_count_override is not None:
            return self.byte_count_override
        return 1 if self.indexed else self.variable.size


ElfSymbol = ElfVariable


@dataclass(frozen=True)
class SignalAlias:
    name: str
    base: str | None = None
    offset: int = 0
    size: int | None = None
    address: int | None = None
    signed: bool | None = None
    note: str = ""


class ElfVariableTable:
    """Indexed view of variables parsed from an ELF file.

    Attributes:
        variables: Flat list of all parsed variables.
        by_name: Dict mapping a name to every matching variable. Static symbols
            can legitimately share a name, so this is the lossless lookup.
        best_by_name: Dict mapping a name to the preferred variable for simple
            read/write operations.
    """

    def __init__(self, variables: Iterable[ElfVariable]) -> None:
        self.variables = sorted(
            variables,
            key=lambda item: (item.name, item.address, item.section_name, item.source),
        )
        self.by_name: dict[str, list[ElfVariable]] = {}
        for variable in self.variables:
            self.by_name.setdefault(variable.name, []).append(variable)
        self.best_by_name = {
            name: self._pick_best(matches) for name, matches in self.by_name.items()
        }

    @classmethod
    def from_elf(
        cls,
        elf_path: Path,
        *,
        include_zero_size: bool = False,
        include_notype: bool = False,
        include_dwarf: bool = False,
    ) -> "ElfVariableTable":
        with elf_path.open("rb") as file:
            elf = ELFFile(file)
            dwarf_variables = _dwarf_variables(elf) if include_dwarf else {}
            variables = list(
                _variables_from_symbol_tables(
                    elf,
                    dwarf_variables,
                    include_zero_size=include_zero_size,
                    include_notype=include_notype,
                )
            )
            known = {
                (item.name, item.address, item.section_name)
                for item in variables
            }
            for item in _flatten_dwarf_variables(dwarf_variables):
                key = (item.name, item.address, item.section_name)
                if key not in known and (include_zero_size or item.size):
                    variables.append(item)
                    known.add(key)
        return cls(_dedupe_variables(variables))

    def get(self, name: str) -> ElfVariable:
        try:
            return self.best_by_name[name]
        except KeyError as exc:
            raise SystemExit(f"ELF variable not found: {name}") from exc

    def find(self, name: str) -> list[ElfVariable]:
        return self.by_name.get(name, [])

    @staticmethod
    def _pick_best(matches: list[ElfVariable]) -> ElfVariable:
        return sorted(
            matches,
            key=lambda item: (
                item.size == 0,
                item.address == 0,
                item.binding == "STB_LOCAL",
                "DWARF" not in item.source,
            ),
        )[0]


class DasError(RuntimeError):
    pass


def _cstr(value: bytes | bytearray) -> str:
    return bytes(value).split(b"\0", 1)[0].decode("latin1", "replace")


def _describe_error(error: int) -> str:
    if error == 0:
        return "OK"
    names = [name for bit, name in DAS_ERROR_NAMES.items() if error & bit]
    return f"0x{error:08x}" + (f" ({'|'.join(names)})" if names else "")


def _parse_int(text: str) -> int:
    return int(text.replace("_", ""), 0)


def _optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return _parse_int(value)
    raise ValueError(f"alias field '{field_name}' must be an int or string")


def _load_signal_aliases(alias_path: str | Path | None) -> dict[str, SignalAlias]:
    if alias_path is None:
        return {}
    path = Path(alias_path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        raw = json.load(file)
    if not isinstance(raw, dict):
        raise ValueError(f"alias file must contain a JSON object: {path}")

    aliases: dict[str, SignalAlias] = {}
    for name, item in raw.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"alias name must be a non-empty string: {name!r}")
        if isinstance(item, str):
            aliases[name] = SignalAlias(name=name, base=item)
            continue
        if not isinstance(item, dict):
            raise ValueError(f"alias '{name}' must be an object or base symbol string")
        base = item.get("base")
        address = _optional_int(item.get("address"), "address")
        if base is not None and not isinstance(base, str):
            raise ValueError(f"alias '{name}' field 'base' must be a string")
        if not base and address is None:
            raise ValueError(f"alias '{name}' must define either 'base' or 'address'")
        aliases[name] = SignalAlias(
            name=name,
            base=base,
            offset=_optional_int(item.get("offset", 0), "offset") or 0,
            size=_optional_int(item.get("size"), "size"),
            address=address,
            signed=item.get("signed"),
            note=str(item.get("note", "")),
        )
    return aliases


def _parse_hex_bytes(text: str) -> bytes:
    raw = text.strip().replace(" ", "").replace("_", "")
    if raw.startswith("0x"):
        raw = raw[2:]
    if len(raw) % 2:
        raw = "0" + raw
    return bytes.fromhex(raw)


def _parse_init_option(value: str) -> int:
    names = {
        "hot": DAS_DIO_HOT_ATTACH,
        "reset-halt": DAS_DIO_RESET_AND_HALT,
    }
    if value in names:
        return names[value]
    return _parse_int(value)


def _split_variable_reference(expression: str) -> tuple[str, int | None]:
    match = VARIABLE_REF_PATTERN.match(expression)
    if not match:
        raise ValueError(f"invalid variable reference: {expression}")
    name = match.group("name")
    index = match.group("index")
    if not name:
        raise ValueError(f"invalid variable reference: {expression}")
    return name, _parse_int(index) if index is not None else None


def _value_to_bytes(value: int | bytes | bytearray | str, byte_count: int) -> bytes:
    if isinstance(value, bytes):
        data = value
    elif isinstance(value, bytearray):
        data = bytes(value)
    elif isinstance(value, str):
        int_value = _parse_int(value)
        data = int_value.to_bytes(byte_count, "little", signed=int_value < 0)
    else:
        data = int(value).to_bytes(byte_count, "little", signed=int(value) < 0)
    if len(data) != byte_count:
        raise ValueError(f"value has {len(data)} byte(s), expected {byte_count}")
    return data


class DasClient:
    def __init__(
        self,
        das_home: str,
        host: str,
        server_index: int,
        port_type: int,
        port_sel: int,
        device_sel: int,
        init_option: int,
        map_without_reset: bool,
    ) -> None:
        self.das_home = das_home
        self.host = host
        self.server_index = server_index
        self.port_type = port_type
        self.port_sel = port_sel
        self.device_sel = device_sel
        self.init_option = init_option
        self.map_option = DAS_MPO_CTDO_NO_RST if map_without_reset else DAS_MPO_DEFAULT
        self.port: int | None = None

        lib_path = Path(das_home) / "lib" / "libdas_api.so"
        self.lib = CDLL(str(lib_path))
        self.lib.das_api_load.argtypes = [c_uint, POINTER(c_uint)]
        self.lib.das_api_load.restype = POINTER(DasApi)

        error = c_uint(0)
        api_ptr = self.lib.das_api_load(4, byref(error))
        if not api_ptr or error.value:
            raise DasError(f"das_api_load failed: {_describe_error(error.value)}")
        self.api = api_ptr.contents

        self._init = CFUNCTYPE(None, POINTER(DasClientInfo), POINTER(c_uint))(self.api.init)
        self._get_servers = CFUNCTYPE(
            None, c_char_p, POINTER(DasServersOnHostList), POINTER(c_uint)
        )(self.api.get_das_servers)
        self._open_port = CFUNCTYPE(
            c_void_p, c_uint, POINTER(DasServerInfo), POINTER(DasKey), POINTER(c_uint)
        )(self.api.open_port)
        self._map_port = CFUNCTYPE(None, c_void_p, c_uint, c_uint, c_uint, POINTER(c_uint))(
            self.api.map_port
        )
        self._connect_to_device = CFUNCTYPE(
            None, c_void_p, c_uint8, POINTER(DasDeviceInfo), POINTER(c_uint)
        )(self.api.connect_to_device)
        self._init_device = CFUNCTYPE(None, c_void_p, c_void_p, c_uint, POINTER(c_uint))(
            self.api.init_device
        )
        self._close_port = CFUNCTYPE(None, c_void_p, POINTER(c_uint))(self.api.close_port)
        self._send_list = CFUNCTYPE(None, c_void_p, POINTER(DasList), POINTER(c_uint))(
            self.api.send_list
        )
        self._wait_list = CFUNCTYPE(
            None, c_void_p, c_uint, POINTER(DasList), POINTER(c_uint)
        )(self.api.wait_list)

        client_info = DasClientInfo()
        client_info.name = b"TC397Tools"
        client_info.manufacturer_name = b"Local"
        client_info.version_major = 0
        client_info.version_minor = 1
        client_info.das_api_v_major = 4
        client_info.das_api_v_minor = 3
        client_info.date = b"May 26 2026"

        error = c_uint(0)
        self._init(byref(client_info), byref(error))
        if error.value:
            raise DasError(f"das init failed: {_describe_error(error.value)}")

    def servers(self) -> DasServersOnHostList:
        server_list = DasServersOnHostList()
        error = c_uint(0)
        self._get_servers(self.host.encode(), byref(server_list), byref(error))
        if error.value:
            raise DasError(f"get_das_servers failed: {_describe_error(error.value)}")
        return server_list

    def open(self) -> DasDeviceInfo:
        server_list = self.servers()
        if self.server_index >= server_list.n_das_servers:
            raise DasError(
                f"server index {self.server_index} out of range; found "
                f"{server_list.n_das_servers} server(s)"
            )
        server = server_list.si[self.server_index]
        if self.port_sel >= server.ports[self.port_type]:
            raise DasError(
                f"port select {self.port_sel} out of range for port type "
                f"{self.port_type}; found {server.ports[self.port_type]} port(s)"
            )

        key = DasKey((c_uint32 * 4)(0, 0, 0, 0))
        error = c_uint(0)
        self.port = self._open_port(0, byref(server), byref(key), byref(error))
        if not self.port or error.value:
            raise DasError(f"open_port failed: {_describe_error(error.value)}")

        error = c_uint(0)
        self._map_port(
            self.port, self.map_option, self.port_type, self.port_sel, byref(error)
        )
        if error.value:
            self.close()
            raise DasError(f"map_port failed: {_describe_error(error.value)}")

        device_info = DasDeviceInfo()
        error = c_uint(0)
        self._connect_to_device(
            self.port, self.device_sel, byref(device_info), byref(error)
        )
        if error.value:
            self.close()
            raise DasError(
                f"connect_to_device failed: {_describe_error(error.value)} "
                f"(device='{_cstr(device_info.name)}', id0=0x{device_info.id.id0:x})"
            )

        error = c_uint(0)
        self._init_device(self.port, None, self.init_option, byref(error))
        if error.value:
            self.close()
            raise DasError(f"init_device failed: {_describe_error(error.value)}")

        return device_info

    def close(self) -> None:
        if self.port:
            error = c_uint(0)
            self._close_port(self.port, byref(error))
            self.port = None

    def __enter__(self) -> "DasClient":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def read(self, address: int, size: int, addr_map: int) -> bytes:
        if not self.port:
            raise DasError("DAS port is not open")
        chunks: list[bytes] = []
        offset = 0
        while offset < size:
            chunk_size = min(DAS_MAX_TRANSFER_SIZE, size - offset)
            buffer = create_string_buffer(chunk_size)
            self._execute_transaction(
                action=DAS_TRA_R | DAS_TRA_BYTE | DAS_TRA_RW_TRANSACTION,
                addr_map=addr_map,
                address=address + offset,
                payload=buffer,
                size=chunk_size,
            )
            chunks.append(buffer.raw)
            offset += chunk_size
        return b"".join(chunks)

    def write(self, address: int, data: bytes, addr_map: int) -> None:
        if not self.port:
            raise DasError("DAS port is not open")
        offset = 0
        while offset < len(data):
            chunk = data[offset : offset + DAS_MAX_TRANSFER_SIZE]
            buffer = create_string_buffer(chunk, len(chunk))
            self._execute_transaction(
                action=DAS_TRA_W | DAS_TRA_BYTE | DAS_TRA_RW_TRANSACTION,
                addr_map=addr_map,
                address=address + offset,
                payload=buffer,
                size=len(chunk),
            )
            offset += len(chunk)

    def _execute_transaction(
        self, action: int, addr_map: int, address: int, payload, size: int
    ) -> None:
        tx = DasTransaction()
        tx.action = action
        tx.addr_map = addr_map
        tx.n_bytes = size
        tx.status = 0
        tx.error = 0
        tx.n_bytes_ok = 0
        tx.address = address & 0xFFFFFFFF
        tx.data = c_void_p(addressof(payload))

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
                "DAS transaction failed: "
                f"api={_describe_error(error.value)} "
                f"list_status=0x{tx_list.status:x} "
                f"tx_status=0x{done.status:x} "
                f"tx_error={_describe_error(done.error)} "
                f"n_bytes_ok={done.n_bytes_ok}/{size}"
            )


class TasTool:
    """High-level TAS/DAS access class for Infineon DAP miniWiggler.

    This class owns TAS server discovery/startup and the target connection. It
    exposes address-based read/write operations that higher layers can reuse.
    """

    def __init__(
        self,
        *,
        das_home: str | Path = os.environ.get("DAS_HOME", DEFAULT_DAS_HOME),
        host: str = "127.0.0.1",
        server_index: int = 0,
        port_type: int = DAS_PT_JTAG,
        port_sel: int = 0,
        device_sel: int = 0,
        addr_map: int = DAS_AMAP_DEVICE_MIN,
        init_option: int = DAS_DIO_HOT_ATTACH,
        map_without_reset: bool = False,
        tas_server_cmd: str | Path | None = None,
    ) -> None:
        self.das_home = str(das_home)
        self.host = host
        self.server_index = server_index
        self.port_type = port_type
        self.port_sel = port_sel
        self.device_sel = device_sel
        self.addr_map = addr_map
        self.init_option = init_option
        self.map_without_reset = map_without_reset
        self.tas_server_cmd = (
            str(tas_server_cmd)
            if tas_server_cmd
            else str(Path(self.das_home) / "bin" / "tas_server")
        )
        self._client: DasClient | None = None
        self._tas_process: subprocess.Popen | None = None
        self.device_info: DasDeviceInfo | None = None

    def _new_client(self, *, port_sel: int | None = None) -> DasClient:
        return DasClient(
            das_home=self.das_home,
            host=self.host,
            server_index=self.server_index,
            port_type=self.port_type,
            port_sel=self.port_sel if port_sel is None else port_sel,
            device_sel=self.device_sel,
            init_option=self.init_option,
            map_without_reset=self.map_without_reset,
        )

    def initialize_tas_server(self, timeout_s: float = 5.0) -> DasServersOnHostList:
        """Ensure tas_server is running and return discovered DAS servers."""

        client = self._new_client()
        try:
            servers = client.servers()
            if servers.n_das_servers:
                return servers
        except DasError:
            pass

        cmd = self.tas_server_cmd
        if not Path(cmd).exists():
            cmd = "tas_server"
        self._tas_process = subprocess.Popen(
            [str(cmd)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        deadline = time.monotonic() + timeout_s
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            time.sleep(0.2)
            try:
                servers = client.servers()
                if servers.n_das_servers:
                    return servers
            except DasError as exc:
                last_error = exc
        if last_error:
            raise DasError(f"tas_server did not become ready: {last_error}") from last_error
        raise DasError("tas_server did not expose any DAS server")

    def servers(self) -> DasServersOnHostList:
        return self._new_client().servers()

    def connect(self) -> DasDeviceInfo:
        self.initialize_tas_server()
        self.disconnect()
        self._client = self._new_client()
        self.device_info = self._client.open()
        return self.device_info

    def disconnect(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
        self.device_info = None

    def __enter__(self) -> "TasTool":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.disconnect()

    def read_address(
        self, address: int, byte_count: int, *, addr_map: int | None = None
    ) -> bytes:
        if not self._client:
            raise DasError("DAP miniWiggler is not connected; call connect() first")
        return self._client.read(
            address, byte_count, self.addr_map if addr_map is None else addr_map
        )

    def write_address(
        self,
        address: int,
        byte_count: int,
        value: int | bytes | bytearray | str,
        *,
        addr_map: int | None = None,
    ) -> None:
        if not self._client:
            raise DasError("DAP miniWiggler is not connected; call connect() first")
        self._client.write(
            address,
            _value_to_bytes(value, byte_count),
            self.addr_map if addr_map is None else addr_map,
        )

    def probe_scan(self) -> list[tuple[int, str, DasDeviceInfo | None]]:
        servers = self.initialize_tas_server()
        server = servers.si[self.server_index]
        results: list[tuple[int, str, DasDeviceInfo | None]] = []
        for sel in range(server.ports[self.port_type]):
            client = self._new_client(port_sel=sel)
            try:
                info = client.open()
                results.append((sel, "OK", info))
            except DasError as exc:
                results.append((sel, str(exc), None))
            finally:
                client.close()
        return results


def _iter_symbol_tables(elf: ELFFile) -> Iterable[SymbolTableSection]:
    for section in elf.iter_sections():
        if isinstance(section, SymbolTableSection):
            yield section


def _section_name(elf: ELFFile, shndx) -> tuple[str, str]:
    if isinstance(shndx, int):
        return str(shndx), elf.get_section(shndx).name
    return str(shndx), str(shndx)


def _is_variable_symbol(symbol_type: str, include_notype: bool) -> bool:
    variable_types = {"STT_OBJECT", "STT_COMMON", "STT_TLS"}
    if include_notype:
        variable_types.add("STT_NOTYPE")
    return symbol_type in variable_types


def _dedupe_variables(variables: Iterable[ElfVariable]) -> list[ElfVariable]:
    result: list[ElfVariable] = []
    seen: set[tuple[str, int, int, str, str, str]] = set()
    for variable in variables:
        key = (
            variable.name,
            variable.address,
            variable.size,
            variable.section_name,
            variable.symbol_type,
            variable.binding,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(variable)
    return result


def _flatten_dwarf_variables(
    dwarf_variables: dict[str, list[ElfVariable]]
) -> Iterable[ElfVariable]:
    for matches in dwarf_variables.values():
        yield from matches


def _variables_from_symbol_tables(
    elf: ELFFile,
    dwarf_variables: dict[str, list[ElfVariable]],
    *,
    include_zero_size: bool,
    include_notype: bool,
) -> Iterable[ElfVariable]:
    for table in _iter_symbol_tables(elf):
        for symbol in table.iter_symbols():
            entry = symbol.entry
            name = symbol.name
            if not name or entry["st_shndx"] == "SHN_UNDEF":
                continue
            symbol_type = entry["st_info"]["type"]
            if not _is_variable_symbol(symbol_type, include_notype):
                continue
            size = int(entry["st_size"])
            if size == 0 and not include_zero_size:
                dwarf_size = _best_dwarf_match(dwarf_variables.get(name, []), int(entry["st_value"]))
                if dwarf_size and dwarf_size.size:
                    size = dwarf_size.size
                else:
                    continue
            section_index, section_name = _section_name(elf, entry["st_shndx"])
            dwarf_match = _best_dwarf_match(dwarf_variables.get(name, []), int(entry["st_value"]))
            source = table.name
            if dwarf_match:
                source = f"{source}+DWARF"
            yield ElfVariable(
                name=name,
                address=int(entry["st_value"]),
                size=size,
                signed=dwarf_match.signed if dwarf_match else None,
                source=source,
                symbol_type=symbol_type,
                binding=entry["st_info"]["bind"],
                visibility=entry["st_other"]["visibility"],
                section_index=section_index,
                section_name=section_name,
                type_name=dwarf_match.type_name if dwarf_match else "",
            )


def _best_dwarf_match(matches: list[ElfVariable], address: int) -> ElfVariable | None:
    if not matches:
        return None
    exact = [item for item in matches if item.address == address]
    candidates = exact or matches
    candidates.sort(key=lambda item: (item.size == 0, item.address == 0))
    return candidates[0]


def _symbol_from_tables(elf: ELFFile, name: str) -> ElfSymbol | None:
    matches = []
    for section in _iter_symbol_tables(elf):
        for symbol in section.iter_symbols():
            if symbol.name != name:
                continue
            entry = symbol.entry
            if entry["st_shndx"] == "SHN_UNDEF":
                continue
            matches.append(
                ElfSymbol(
                    name=name,
                    address=int(entry["st_value"]),
                    size=int(entry["st_size"]),
                    source=section.name,
                    symbol_type=entry["st_info"]["type"],
                    binding=entry["st_info"]["bind"],
                    visibility=entry["st_other"]["visibility"],
                    section_index=_section_name(elf, entry["st_shndx"])[0],
                    section_name=_section_name(elf, entry["st_shndx"])[1],
                )
            )
    if not matches:
        return None
    matches.sort(key=lambda item: (item.size == 0, item.address == 0))
    return matches[0]


def _resolve_ref_die(die, attr_name: str):
    attr = die.attributes.get(attr_name)
    if not attr:
        return None
    form_class = describe_form_class(attr.form)
    if form_class == "reference":
        return die.get_DIE_from_attribute(attr_name)
    return None


def _type_info_from_die(die) -> tuple[int | None, bool | None, str]:
    seen = set()
    current = die
    last_name = ""
    while current is not None and current.offset not in seen:
        seen.add(current.offset)
        name = current.attributes.get("DW_AT_name")
        if name:
            last_name = name.value.decode(errors="replace")
        byte_size = current.attributes.get("DW_AT_byte_size")
        encoding = current.attributes.get("DW_AT_encoding")
        if byte_size:
            signed = None
            if encoding:
                signed = encoding.value in (0x05, 0x06)
            return int(byte_size.value), signed, last_name
        current = _resolve_ref_die(current, "DW_AT_type")
    return None, None, last_name


def _die_name(die) -> str:
    attr = die.attributes.get("DW_AT_name")
    if not attr:
        return ""
    return attr.value.decode(errors="replace")


def _dwarf_expr_ops(dwarf, value) -> list:
    return DWARFExprParser(dwarf.structs).parse_expr(value)


def _dwarf_address_from_location(dwarf, die) -> int | None:
    attr = die.attributes.get("DW_AT_location")
    if not attr:
        return None
    form_class = describe_form_class(attr.form)
    if form_class not in {"exprloc", "block"}:
        return None
    ops = _dwarf_expr_ops(dwarf, attr.value)
    if len(ops) == 1 and ops[0].op_name == "DW_OP_addr":
        return int(ops[0].args[0])
    return None


def _dwarf_member_offset(dwarf, member_die) -> int:
    attr = member_die.attributes.get("DW_AT_data_member_location")
    if not attr:
        return 0
    form_class = describe_form_class(attr.form)
    if form_class == "constant":
        return int(attr.value)
    if form_class not in {"exprloc", "block"}:
        raise ValueError(f"unsupported DWARF member location form: {attr.form}")
    ops = _dwarf_expr_ops(dwarf, attr.value)
    if len(ops) == 1 and ops[0].op_name == "DW_OP_plus_uconst":
        return int(ops[0].args[0])
    if len(ops) == 1 and ops[0].op_name.startswith("DW_OP_lit"):
        return int(ops[0].op_name.removeprefix("DW_OP_lit"))
    raise ValueError(f"unsupported DWARF member location expression: {ops}")


def _dwarf_resolve_type(die):
    current = die
    seen = set()
    wrapper_tags = {
        "DW_TAG_typedef",
        "DW_TAG_const_type",
        "DW_TAG_volatile_type",
        "DW_TAG_restrict_type",
        "DW_TAG_atomic_type",
    }
    while current is not None and current.offset not in seen:
        seen.add(current.offset)
        if current.tag not in wrapper_tags:
            return current
        current = _resolve_ref_die(current, "DW_AT_type")
    return current


def _dwarf_variables(elf: ELFFile) -> dict[str, list[ElfVariable]]:
    if not elf.has_dwarf_info():
        return {}
    dwarf = elf.get_dwarf_info()
    variables: dict[str, list[ElfVariable]] = {}
    for cu in dwarf.iter_CUs():
        for die in cu.iter_DIEs():
            if die.tag != "DW_TAG_variable":
                continue
            name_attr = die.attributes.get("DW_AT_name")
            if not name_attr:
                continue
            name = name_attr.value.decode(errors="replace")
            address = _dwarf_address_from_location(dwarf, die)
            if address is None:
                continue
            type_die = _resolve_ref_die(die, "DW_AT_type")
            size, signed, type_name = (
                _type_info_from_die(type_die) if type_die else (None, None, "")
            )
            variable = ElfVariable(
                name=name,
                address=address,
                size=size or 0,
                signed=signed,
                source="DWARF",
                symbol_type="DW_TAG_variable",
                section_index="",
                section_name="",
                type_name=type_name,
            )
            variables.setdefault(name, []).append(variable)
    return variables


def _symbol_from_dwarf(elf: ELFFile, name: str) -> ElfSymbol | None:
    return _best_dwarf_match(_dwarf_variables(elf).get(name, []), 0)


def resolve_symbol(elf_path: Path, name: str) -> ElfSymbol:
    return ElfParser(elf_path, include_zero_size=True).get_variable(name)


def _load_fast_elfio_resolver():
    module_path = Path(__file__).resolve().with_name("tc397_elfio_fast.py")
    if module_path.exists():
        spec = importlib.util.spec_from_file_location("tc397_elfio_fast", module_path)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
                return module
            except Exception:
                return None
    try:
        import tc397_elfio_fast

        return tc397_elfio_fast
    except Exception:
        return None


class ElfParser:
    """ELF variable parser built on pyelftools."""

    def __init__(
        self,
        elf_path: str | Path,
        *,
        include_zero_size: bool = False,
        include_notype: bool = False,
        include_dwarf: bool = False,
        alias_path: str | Path | None = DEFAULT_SIGNAL_ALIAS_PATH,
    ) -> None:
        self.elf_path = Path(elf_path)
        self.include_zero_size = include_zero_size
        self.include_notype = include_notype
        self.include_dwarf = include_dwarf
        self.alias_path = Path(alias_path) if alias_path is not None else None
        self.signal_aliases = _load_signal_aliases(self.alias_path)
        self.variable_table: ElfVariableTable | None = None
        self._dwarf_file = None
        self._dwarf_info = None
        self._debug_info_data: bytes | None = None
        self._reference_cache: dict[str, ElfVariableReference] = {}
        self._candidate_cu_cache: dict[str, list] = {}
        self._dwarf_variable_die_cache: dict[str, Any] = {}
        self._fast_elfio_resolver = None

    def parse(self, *, force: bool = False) -> ElfVariableTable:
        if force:
            self._reference_cache.clear()
            self._candidate_cu_cache.clear()
            self._dwarf_variable_die_cache.clear()
            self.variable_table = None
        if self.variable_table is None:
            self.variable_table = ElfVariableTable.from_elf(
                self.elf_path,
                include_zero_size=self.include_zero_size,
                include_notype=self.include_notype,
                include_dwarf=self.include_dwarf,
            )
        return self.variable_table

    @property
    def variables(self) -> list[ElfVariable]:
        return self.parse().variables

    @property
    def by_name(self) -> dict[str, list[ElfVariable]]:
        return self.parse().by_name

    @property
    def best_by_name(self) -> dict[str, ElfVariable]:
        return self.parse().best_by_name

    def get_variable(self, name: str) -> ElfVariable:
        alias = self.get_variable_reference(name) if self.is_signal_alias(name) else None
        if alias:
            return ElfVariable(
                name=name,
                address=alias.address,
                size=alias.default_byte_count,
                signed=alias.variable.signed,
                source=alias.variable.source,
                symbol_type=alias.variable.symbol_type,
                binding=alias.variable.binding,
                visibility=alias.variable.visibility,
                section_index=alias.variable.section_index,
                section_name=alias.variable.section_name,
                type_name=alias.variable.type_name,
            )
        base_name, _ = _split_variable_reference(name)
        if "." in base_name:
            reference = self.get_variable_reference(name)
            return ElfVariable(
                name=name,
                address=reference.address,
                size=reference.default_byte_count,
                signed=reference.variable.signed,
                source=reference.variable.source,
                symbol_type=reference.variable.symbol_type,
                binding=reference.variable.binding,
                visibility=reference.variable.visibility,
                section_index=reference.variable.section_index,
                section_name=reference.variable.section_name,
                type_name=reference.variable.type_name,
            )
        return self.parse().get(base_name)

    def is_signal_alias(self, expression: str) -> bool:
        base_name, _ = _split_variable_reference(expression)
        return base_name in self.signal_aliases

    def _get_dwarf_info(self):
        if self._dwarf_info is None:
            self._dwarf_file = self.elf_path.open("rb")
            elf = ELFFile(self._dwarf_file)
            if not elf.has_dwarf_info():
                return None
            self._dwarf_info = elf.get_dwarf_info()
        return self._dwarf_info

    def _get_debug_info_data(self) -> bytes | None:
        if self._debug_info_data is None:
            with self.elf_path.open("rb") as file:
                elf = ELFFile(file)
                section = elf.get_section_by_name(".debug_info")
                if section is None:
                    return None
                self._debug_info_data = section.data()
        return self._debug_info_data

    def _candidate_cus_for_name(self, name: str):
        if name in self._candidate_cu_cache:
            return self._candidate_cu_cache[name]

        dwarf = self._get_dwarf_info()
        data = self._get_debug_info_data()
        if dwarf is None or data is None:
            return []

        needle = name.encode()
        offsets: list[int] = []
        start = 0
        while True:
            offset = data.find(needle, start)
            if offset < 0:
                break
            after = offset + len(needle)
            after_ok = after >= len(data) or data[after] == 0
            if after_ok:
                offsets.append(offset)
            start = offset + 1

        cus = []
        seen: set[int] = set()
        for offset in offsets:
            cu = dwarf.get_CU_containing(offset)
            if cu.cu_offset in seen:
                continue
            seen.add(cu.cu_offset)
            cus.append(cu)
        self._candidate_cu_cache[name] = cus
        return cus

    def _find_dwarf_variable_die(self, name: str):
        if name in self._dwarf_variable_die_cache:
            return self._dwarf_variable_die_cache[name]

        for cu in self._candidate_cus_for_name(name):
            for die in cu.iter_DIEs():
                if die.tag == "DW_TAG_variable" and _die_name(die) == name:
                    self._dwarf_variable_die_cache[name] = die
                    return die
        self._dwarf_variable_die_cache[name] = None
        return None

    def _dwarf_field_reference(self, expression: str) -> ElfVariableReference | None:
        base_expression, index = _split_variable_reference(expression)
        parts = base_expression.split(".")
        if len(parts) < 2:
            return None
        root_name = parts[0]
        field_names = parts[1:]

        dwarf = self._get_dwarf_info()
        if dwarf is None:
            return None
        root_die = self._find_dwarf_variable_die(root_name)
        if root_die is None:
            return None
        root_address = _dwarf_address_from_location(dwarf, root_die)
        if root_address is None:
            return None

        root_type = _dwarf_resolve_type(_resolve_ref_die(root_die, "DW_AT_type"))
        root_size, signed, root_type_name = _type_info_from_die(root_type)
        if root_type is None:
            return None

        byte_offset = 0
        current_type = root_type
        final_size = root_size or 0
        final_signed = signed
        final_type_name = root_type_name
        traversed = [root_name]
        for field_name in field_names:
            current_type = _dwarf_resolve_type(current_type)
            if current_type is None:
                return None
            member = None
            for child in current_type.iter_children():
                if child.tag == "DW_TAG_member" and _die_name(child) == field_name:
                    member = child
                    break
            if member is None:
                missing_path = ".".join(traversed + [field_name])
                raise ValueError(f"DWARF member not found: {missing_path}")

            byte_offset += _dwarf_member_offset(dwarf, member)
            traversed.append(field_name)
            member_type = _dwarf_resolve_type(_resolve_ref_die(member, "DW_AT_type"))
            member_size = member.attributes.get("DW_AT_byte_size")
            type_size, type_signed, type_name = (
                _type_info_from_die(member_type) if member_type else (None, None, "")
            )
            final_size = int(member_size.value) if member_size else (type_size or 0)
            final_signed = type_signed
            final_type_name = type_name
            current_type = member_type

        if index is not None and index < 0:
            raise ValueError(f"negative byte index is not supported: {expression}")
        if index is not None and final_size and index >= final_size:
            raise ValueError(
                f"byte index {index} is outside {base_expression} size {final_size}"
            )

        variable = ElfVariable(
            name=root_name,
            address=root_address,
            size=root_size or 0,
            signed=final_signed,
            source="DWARF.field",
            symbol_type="DW_TAG_variable",
            type_name=final_type_name,
        )
        return ElfVariableReference(
            expression=expression,
            variable=variable,
            byte_offset=byte_offset + (index or 0),
            indexed=index is not None,
            byte_count_override=1 if index is not None else final_size,
        )

    def _alias_reference(self, expression: str) -> ElfVariableReference | None:
        base_name, index = _split_variable_reference(expression)
        alias = self.signal_aliases.get(base_name)
        if not alias:
            return None
        if index is not None and index < 0:
            raise ValueError(f"negative byte index is not supported: {expression}")
        if index is not None and alias.size is not None and index >= alias.size:
            raise ValueError(f"byte index {index} is outside {base_name} size {alias.size}")

        if alias.base:
            variable = self.parse().get(alias.base)
            byte_offset = alias.offset + (index or 0)
            if variable.size and byte_offset >= variable.size:
                raise ValueError(
                    f"alias {base_name} offset {byte_offset} exceeds base "
                    f"{alias.base} size {variable.size}"
                )
        else:
            variable = ElfVariable(
                name=base_name,
                address=alias.address or 0,
                size=alias.size or 0,
                signed=alias.signed,
                source="alias",
                symbol_type="SIGNAL_ALIAS",
            )
            byte_offset = index or 0

        return ElfVariableReference(
            expression=expression,
            variable=variable,
            byte_offset=byte_offset,
            indexed=index is not None,
            byte_count_override=1 if index is not None else alias.size,
        )

    def _fast_elfio_reference(self, expression: str) -> ElfVariableReference | None:
        fast_module = _load_fast_elfio_resolver()
        if fast_module is None or not fast_module.available():
            return None
        try:
            if self._fast_elfio_resolver is None:
                self._fast_elfio_resolver = fast_module.ElfioResolver(self.elf_path)
            info = self._fast_elfio_resolver.resolve_reference(
                expression,
                include_zero_size=self.include_zero_size,
                include_notype=self.include_notype,
            )
        except Exception:
            return None

        byte_size = int(info["byte_size"])
        variable_size = int(info["variable_size"])
        signed = info.get("signed")
        variable = ElfVariable(
            name=str(info["base_name"]),
            address=int(info["address"]),
            size=variable_size,
            signed=signed if isinstance(signed, bool) else None,
            source=str(info["source"]),
            symbol_type=str(info["symbol_type"]),
            binding=str(info["binding"]),
            section_name=str(info["section_name"]),
            type_name=str(info["type_name"]),
        )
        return ElfVariableReference(
            expression=expression,
            variable=variable,
            byte_offset=0,
            indexed=bool(info["indexed"]),
            byte_count_override=byte_size,
        )

    def get_variable_reference(self, expression: str) -> ElfVariableReference:
        cached = self._reference_cache.get(expression)
        if cached is not None:
            return cached

        alias_reference = self._alias_reference(expression)
        if alias_reference:
            self._reference_cache[expression] = alias_reference
            return alias_reference
        fast_reference = self._fast_elfio_reference(expression)
        if fast_reference:
            self._reference_cache[expression] = fast_reference
            return fast_reference
        dwarf_field_reference = self._dwarf_field_reference(expression)
        if dwarf_field_reference:
            self._reference_cache[expression] = dwarf_field_reference
            return dwarf_field_reference
        base_name, index = _split_variable_reference(expression)
        variable = self.parse().get(base_name)
        byte_offset = index or 0
        if index is not None and index < 0:
            raise ValueError(f"negative byte index is not supported: {expression}")
        if index is not None and variable.size and index >= variable.size:
            raise ValueError(
                f"byte index {index} is outside {base_name} size {variable.size}"
            )
        reference = ElfVariableReference(
            expression=expression,
            variable=variable,
            byte_offset=byte_offset,
            indexed=index is not None,
        )
        self._reference_cache[expression] = reference
        return reference

    def find_variables(self, name: str) -> list[ElfVariable]:
        return self.parse().find(name)

    def get_variable_address_size(self, name: str) -> tuple[int, int]:
        reference = self.get_variable_reference(name)
        return reference.address, reference.default_byte_count


class Tc397VariableAccess:
    """Use TAS/DAS and ELF metadata together for variable read/write."""

    def __init__(
        self,
        elf_path: str | Path,
        *,
        tas_tool: TasTool | None = None,
        elf_parser: ElfParser | None = None,
        **tas_kwargs,
    ) -> None:
        self.elf_parser = elf_parser or ElfParser(elf_path, include_zero_size=True)
        self.tas_tool = tas_tool or TasTool(**tas_kwargs)

    def connect(self) -> DasDeviceInfo:
        return self.tas_tool.connect()

    def disconnect(self) -> None:
        self.tas_tool.disconnect()

    def __enter__(self) -> "Tc397VariableAccess":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.disconnect()

    def get_variable(self, name: str) -> ElfVariable:
        return self.elf_parser.get_variable(name)

    def get_variable_reference(self, expression: str) -> ElfVariableReference:
        return self.elf_parser.get_variable_reference(expression)

    def _resolve_access(
        self, expression: str, byte_count: int | None, offset: int
    ) -> tuple[ElfVariableReference, int, int]:
        reference = self.get_variable_reference(expression)
        address, size = self._resolve_reference_access(reference, byte_count, offset)
        return reference, address, size

    def _resolve_reference_access(
        self,
        reference: ElfVariableReference,
        byte_count: int | None,
        offset: int,
    ) -> tuple[int, int]:
        expression = reference.expression
        size = byte_count or reference.default_byte_count
        total_offset = reference.byte_offset + offset
        if size <= 0:
            raise ValueError(f"variable size is unknown for {expression}; pass byte_count")
        if total_offset < 0:
            raise ValueError(f"negative byte offset is not supported: {expression}")
        if (
            reference.byte_count_override is not None
            and offset + size > reference.byte_count_override
        ):
            raise ValueError(
                f"access {expression} offset={offset} size={size} exceeds "
                f"reference size {reference.byte_count_override}"
            )
        if reference.variable.size and total_offset + size > reference.variable.size:
            raise ValueError(
                f"access {expression} offset={total_offset} size={size} exceeds "
                f"variable size {reference.variable.size}"
            )
        return reference.variable.address + total_offset, size

    def read_variable(
        self,
        name: str,
        *,
        byte_count: int | None = None,
        offset: int = 0,
        addr_map: int | None = None,
    ) -> bytes:
        _, address, size = self._resolve_access(name, byte_count, offset)
        return self.tas_tool.read_address(address, size, addr_map=addr_map)

    def write_variable(
        self,
        name: str,
        value: int | bytes | bytearray | str,
        *,
        byte_count: int | None = None,
        offset: int = 0,
        addr_map: int | None = None,
    ) -> None:
        _, address, size = self._resolve_access(name, byte_count, offset)
        self.tas_tool.write_address(address, size, value, addr_map=addr_map)

    def read_reference(
        self,
        reference: ElfVariableReference,
        *,
        byte_count: int | None = None,
        offset: int = 0,
        addr_map: int | None = None,
    ) -> bytes:
        address, size = self._resolve_reference_access(reference, byte_count, offset)
        return self.tas_tool.read_address(address, size, addr_map=addr_map)

    def write_reference(
        self,
        reference: ElfVariableReference,
        value: int | bytes | bytearray | str,
        *,
        byte_count: int | None = None,
        offset: int = 0,
        addr_map: int | None = None,
    ) -> None:
        address, size = self._resolve_reference_access(reference, byte_count, offset)
        self.tas_tool.write_address(address, size, value, addr_map=addr_map)

    def read_variable_value(
        self,
        name: str,
        *,
        byte_count: int | None = None,
        signed: bool | None = None,
        offset: int = 0,
        addr_map: int | None = None,
    ) -> int:
        reference = self.get_variable_reference(name)
        data = self.read_variable(
            name, byte_count=byte_count, offset=offset, addr_map=addr_map
        )
        use_signed = reference.variable.signed if signed is None else signed
        return int.from_bytes(data, "little", signed=bool(use_signed))

    def read_reference_value(
        self,
        reference: ElfVariableReference,
        *,
        byte_count: int | None = None,
        signed: bool | None = None,
        offset: int = 0,
        addr_map: int | None = None,
    ) -> int:
        data = self.read_reference(
            reference, byte_count=byte_count, offset=offset, addr_map=addr_map
        )
        use_signed = reference.variable.signed if signed is None else signed
        return int.from_bytes(data, "little", signed=bool(use_signed))


def format_read(symbol_name: str, address: int, size: int, data: bytes, signed: bool | None) -> str:
    unsigned_value = int.from_bytes(data, "little", signed=False)
    parts = [
        f"name={symbol_name}",
        f"address=0x{address:08x}",
        f"size={size}",
        f"bytes={data.hex()}",
        f"uint={unsigned_value}",
        f"hex=0x{unsigned_value:x}",
    ]
    if signed is True and size <= 8:
        parts.append(f"int={int.from_bytes(data, 'little', signed=True)}")
    return " ".join(parts)


def common_tas_tool(args: argparse.Namespace) -> TasTool:
    return TasTool(
        das_home=args.das_home,
        host=args.host,
        server_index=args.server_index,
        port_type=args.port_type,
        port_sel=args.port_sel,
        device_sel=args.device_sel,
        addr_map=args.addr_map,
        init_option=args.init_option,
        map_without_reset=args.map_without_reset,
    )


def cmd_servers(args: argparse.Namespace) -> None:
    tool = common_tas_tool(args)
    server_list = tool.initialize_tas_server()
    print(f"found {server_list.n_das_servers} DAS server(s) on {args.host}")
    for idx in range(server_list.n_das_servers):
        server = server_list.si[idx]
        ports = [(port_type, count) for port_type, count in enumerate(server.ports) if count]
        print(
            f"[{idx}] host={_cstr(server.server_host_addr)}:{_cstr(server.server_host_port)} "
            f"name={_cstr(server.name)} manufacturer={_cstr(server.manufacturer_name)} "
            f"ports={ports}"
        )


def cmd_probe(args: argparse.Namespace) -> None:
    tool = common_tas_tool(args)
    if args.scan:
        server = tool.initialize_tas_server().si[args.server_index]
        count = server.ports[args.port_type]
        print(f"scanning port_type={args.port_type}, count={count}")
        for sel, status, info in tool.probe_scan():
            if info:
                print(
                    f"sel={sel}: OK device={_cstr(info.name)} "
                    f"id0=0x{info.id.id0:x} id1=0x{info.id.id1:x}"
                )
            else:
                print(f"sel={sel}: {status}")
        return

    try:
        info = tool.connect()
        print(
            f"connected device={_cstr(info.name)} "
            f"id0=0x{info.id.id0:x} id1=0x{info.id.id1:x}"
        )
    finally:
        tool.disconnect()


def cmd_symbol(args: argparse.Namespace) -> None:
    reference = ElfParser(
        args.elf,
        include_zero_size=True,
        include_dwarf=args.with_dwarf,
        alias_path=args.alias_file,
    ).get_variable_reference(args.name)
    symbol = reference.variable
    print(
        f"name={args.name} base={symbol.name} address=0x{reference.address:08x} "
        f"size={reference.default_byte_count} variable_size={symbol.size} "
        f"signed={symbol.signed} source={symbol.source} "
        f"section={symbol.section_name} type={symbol.symbol_type} "
        f"binding={symbol.binding} ctype={symbol.type_name}"
    )


def _print_variables(variables: list[ElfVariable]) -> None:
    columns = [
        ("name", 36),
        ("address", 12),
        ("size", 6),
        ("section", 14),
        ("bind", 10),
        ("type", 12),
        ("ctype", 18),
        ("source", 12),
    ]
    header = " ".join(title.ljust(width) for title, width in columns)
    print(header)
    print("-" * len(header))
    for variable in variables:
        values = {
            "name": variable.name,
            "address": f"0x{variable.address:08x}",
            "size": str(variable.size),
            "section": variable.section_name,
            "bind": variable.binding.replace("STB_", ""),
            "type": variable.symbol_type.replace("STT_", ""),
            "ctype": variable.type_name,
            "source": variable.source,
        }
        print(
            " ".join(
                values[title][:width].ljust(width)
                for title, width in columns
            )
        )


def cmd_variables(args: argparse.Namespace) -> None:
    table = ElfParser(
        args.elf,
        include_zero_size=args.include_zero_size,
        include_notype=args.include_notype,
        include_dwarf=args.with_dwarf,
        alias_path=args.alias_file,
    ).parse()
    variables = table.variables
    if args.name_contains:
        variables = [item for item in variables if args.name_contains in item.name]
    if args.section:
        variables = [item for item in variables if item.section_name == args.section]
    if args.address_min is not None:
        variables = [item for item in variables if item.address >= args.address_min]
    if args.address_max is not None:
        variables = [item for item in variables if item.address <= args.address_max]
    if args.limit:
        variables = variables[: args.limit]

    if args.json:
        print(json.dumps([asdict(item) for item in variables], indent=2))
        return

    print(
        f"variables={len(variables)} unique_names={len(table.by_name)} "
        f"elf={Path(args.elf)}"
    )
    _print_variables(variables)


def cmd_read(args: argparse.Namespace) -> None:
    access = Tc397VariableAccess(
        args.elf,
        tas_tool=common_tas_tool(args),
        elf_parser=ElfParser(
            args.elf,
            include_zero_size=True,
            include_dwarf=args.with_dwarf,
            alias_path=args.alias_file,
        ),
    )
    reference = access.get_variable_reference(args.name)
    symbol = reference.variable
    size = args.size or reference.default_byte_count
    if size <= 0:
        raise SystemExit(f"symbol size is unknown for {args.name}; pass --size")
    with access:
        data = access.read_variable(args.name, byte_count=size, offset=args.offset)
    print(format_read(args.name, reference.address + args.offset, size, data, symbol.signed))


def cmd_write(args: argparse.Namespace) -> None:
    access = Tc397VariableAccess(
        args.elf,
        tas_tool=common_tas_tool(args),
        elf_parser=ElfParser(
            args.elf,
            include_zero_size=True,
            include_dwarf=args.with_dwarf,
            alias_path=args.alias_file,
        ),
    )
    reference = access.get_variable_reference(args.name)
    symbol = reference.variable
    size = args.size or reference.default_byte_count
    if size <= 0:
        raise SystemExit(f"symbol size is unknown for {args.name}; pass --size")
    if args.bytes:
        value: int | bytes = _parse_hex_bytes(args.value)
        if len(value) != size:
            raise SystemExit(f"byte value has {len(value)} byte(s), expected {size}")
    else:
        value = _parse_int(args.value)
    with access:
        access.write_variable(args.name, value, byte_count=size, offset=args.offset)
        if args.read_back:
            read_back = access.read_variable(args.name, byte_count=size, offset=args.offset)
            print(format_read(args.name, reference.address + args.offset, size, read_back, symbol.signed))
        else:
            print(f"wrote name={args.name} address=0x{reference.address + args.offset:08x} size={size}")


def cmd_read_addr(args: argparse.Namespace) -> None:
    tool = common_tas_tool(args)
    with tool:
        data = tool.read_address(_parse_int(args.address), args.size)
    print(
        format_read(
            f"@{args.address}",
            _parse_int(args.address),
            args.size,
            data,
            signed=False,
        )
    )


def cmd_write_addr(args: argparse.Namespace) -> None:
    address = _parse_int(args.address)
    value: int | bytes = _parse_hex_bytes(args.value) if args.bytes else _parse_int(args.value)
    if args.bytes and len(value) != args.size:
        raise SystemExit(f"value has {len(value)} byte(s), expected {args.size}")
    tool = common_tas_tool(args)
    with tool:
        tool.write_address(address, args.size, value)
        if args.read_back:
            read_back = tool.read_address(address, args.size)
            print(format_read(f"@{args.address}", address, args.size, read_back, signed=False))
        else:
            print(f"wrote address=0x{address:08x} size={args.size}")


def add_das_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--das-home", default=os.environ.get("DAS_HOME", DEFAULT_DAS_HOME))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--server-index", type=int, default=0)
    parser.add_argument("--port-type", type=int, default=DAS_PT_JTAG)
    parser.add_argument("--port-sel", type=int, default=0)
    parser.add_argument("--device-sel", type=int, default=0)
    parser.add_argument("--addr-map", type=int, default=DAS_AMAP_DEVICE_MIN)
    parser.add_argument(
        "--init-option",
        type=_parse_init_option,
        default=DAS_DIO_HOT_ATTACH,
        help="DAS init option: hot, reset-halt, or numeric value.",
    )
    parser.add_argument(
        "--map-without-reset",
        action="store_true",
        help="Map the port with DAS_MPO_CTDO_NO_RST.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_das_args(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    servers = sub.add_parser("servers", help="List TAS/DAS servers.")
    servers.set_defaults(func=cmd_servers)

    probe = sub.add_parser("probe", help="Open and initialize a DAS target connection.")
    probe.add_argument("--scan", action="store_true", help="Try every port select index.")
    probe.set_defaults(func=cmd_probe)

    symbol = sub.add_parser("symbol", help="Resolve an ELF variable name.")
    symbol.add_argument("elf")
    symbol.add_argument("name")
    symbol.add_argument("--with-dwarf", action="store_true", help="Also scan DWARF debug info.")
    symbol.add_argument("--alias-file", default=DEFAULT_SIGNAL_ALIAS_PATH)
    symbol.set_defaults(func=cmd_symbol)

    variables = sub.add_parser("variables", help="List variables parsed from an ELF.")
    variables.add_argument("elf")
    variables.add_argument("--include-zero-size", action="store_true")
    variables.add_argument("--include-notype", action="store_true")
    variables.add_argument("--with-dwarf", action="store_true", help="Also scan DWARF debug info.")
    variables.add_argument("--alias-file", default=DEFAULT_SIGNAL_ALIAS_PATH)
    variables.add_argument("--name-contains")
    variables.add_argument("--section")
    variables.add_argument("--address-min", type=lambda value: int(value, 0))
    variables.add_argument("--address-max", type=lambda value: int(value, 0))
    variables.add_argument("--limit", type=int)
    variables.add_argument("--json", action="store_true", help="Output JSON.")
    variables.set_defaults(func=cmd_variables)

    read = sub.add_parser("read", help="Read a variable by ELF symbol name.")
    read.add_argument("elf")
    read.add_argument("name")
    read.add_argument("--size", type=int)
    read.add_argument("--offset", type=lambda value: int(value, 0), default=0)
    read.add_argument("--with-dwarf", action="store_true", help="Also scan DWARF debug info.")
    read.add_argument("--alias-file", default=DEFAULT_SIGNAL_ALIAS_PATH)
    read.set_defaults(func=cmd_read)

    write = sub.add_parser("write", help="Write a variable by ELF symbol name.")
    write.add_argument("elf")
    write.add_argument("name")
    write.add_argument("value")
    write.add_argument("--size", type=int)
    write.add_argument("--offset", type=lambda value: int(value, 0), default=0)
    write.add_argument("--bytes", action="store_true", help="Interpret value as hex bytes.")
    write.add_argument("--with-dwarf", action="store_true", help="Also scan DWARF debug info.")
    write.add_argument("--alias-file", default=DEFAULT_SIGNAL_ALIAS_PATH)
    write.add_argument("--no-read-back", dest="read_back", action="store_false")
    write.set_defaults(func=cmd_write, read_back=True)

    read_addr = sub.add_parser("read-addr", help="Read a raw target address.")
    read_addr.add_argument("address")
    read_addr.add_argument("size", type=int)
    read_addr.set_defaults(func=cmd_read_addr)

    write_addr = sub.add_parser("write-addr", help="Write a raw target address.")
    write_addr.add_argument("address")
    write_addr.add_argument("size", type=int)
    write_addr.add_argument("value")
    write_addr.add_argument("--bytes", action="store_true", help="Interpret value as hex bytes.")
    write_addr.add_argument("--no-read-back", dest="read_back", action="store_false")
    write_addr.set_defaults(func=cmd_write_addr, read_back=True)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except DasError as exc:
        raise SystemExit(f"DAS error: {exc}") from exc
    except ValueError as exc:
        raise SystemExit(f"Value error: {exc}") from exc


if __name__ == "__main__":
    # elfinfo = ElfParser("/home/shiheping/QianLiPrj/TC397Tools/Downloads/MCU_A.elf").parse()
    main()
