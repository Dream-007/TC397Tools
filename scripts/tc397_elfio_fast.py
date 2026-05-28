"""ctypes wrapper for the optional C++ ELFIO resolver."""

from __future__ import annotations

import ctypes
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).parent
DEFAULT_LIBRARY = REPO_ROOT / "libtc397_elfio_resolver.so"

_lib: ctypes.CDLL | None = None
_load_error: Exception | None = None


def _library() -> ctypes.CDLL | None:
    global _lib, _load_error
    if _lib is not None:
        return _lib
    if _load_error is not None:
        return None
    try:
        lib = ctypes.CDLL(str(DEFAULT_LIBRARY))
        lib.tc397_elf_resolve.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.c_char_p,
            ctypes.c_size_t,
        ]
        lib.tc397_elf_resolve.restype = ctypes.c_int
        lib.tc397_elf_open.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_size_t,
        ]
        lib.tc397_elf_open.restype = ctypes.c_void_p
        lib.tc397_elf_close.argtypes = [ctypes.c_void_p]
        lib.tc397_elf_close.restype = None
        lib.tc397_elf_resolve_handle.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.c_char_p,
            ctypes.c_size_t,
        ]
        lib.tc397_elf_resolve_handle.restype = ctypes.c_int
        _lib = lib
        return _lib
    except OSError as exc:
        _load_error = exc
        return None


def available() -> bool:
    return _library() is not None


def load_error() -> Exception | None:
    _library()
    return _load_error


def resolve_reference(
    elf_path: str | Path,
    expression: str,
    *,
    include_zero_size: bool = True,
    include_notype: bool = False,
) -> dict[str, Any]:
    lib = _library()
    if lib is None:
        raise RuntimeError(f"C++ ELFIO resolver is not built: {DEFAULT_LIBRARY}")

    out_size = 8192
    err_size = 2048
    out = ctypes.create_string_buffer(out_size)
    err = ctypes.create_string_buffer(err_size)
    rc = lib.tc397_elf_resolve(
        str(elf_path).encode(),
        expression.encode(),
        int(include_zero_size),
        int(include_notype),
        out,
        out_size,
        err,
        err_size,
    )
    if rc != 0:
        raise RuntimeError(err.value.decode(errors="replace"))
    return json.loads(out.value.decode())


class ElfioResolver:
    def __init__(self, elf_path: str | Path) -> None:
        lib = _library()
        if lib is None:
            raise RuntimeError(f"C++ ELFIO resolver is not built: {DEFAULT_LIBRARY}")

        err = ctypes.create_string_buffer(2048)
        handle = lib.tc397_elf_open(str(elf_path).encode(), err, len(err))
        if not handle:
            raise RuntimeError(err.value.decode(errors="replace"))
        self._handle = ctypes.c_void_p(handle)
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            lib = _library()
            if lib is not None and self._handle:
                lib.tc397_elf_close(self._handle)
            self._closed = True

    def __del__(self) -> None:
        self.close()

    def resolve_reference(
        self,
        expression: str,
        *,
        include_zero_size: bool = True,
        include_notype: bool = False,
    ) -> dict[str, Any]:
        lib = _library()
        if lib is None:
            raise RuntimeError(f"C++ ELFIO resolver is not built: {DEFAULT_LIBRARY}")

        out_size = 8192
        err_size = 2048
        out = ctypes.create_string_buffer(out_size)
        err = ctypes.create_string_buffer(err_size)
        rc = lib.tc397_elf_resolve_handle(
            self._handle,
            expression.encode(),
            int(include_zero_size),
            int(include_notype),
            out,
            out_size,
            err,
            err_size,
        )
        if rc != 0:
            raise RuntimeError(err.value.decode(errors="replace"))
        return json.loads(out.value.decode())
