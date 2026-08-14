from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

CRYPTPROTECT_UI_FORBIDDEN = 0x01


class DataProtectionError(OSError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def protect_for_current_user(data: bytes, description: str = "ExamDesk 离线考试系统") -> bytes:
    return _crypt_protect(data, description)


def unprotect_for_current_user(data: bytes) -> bytes:
    if os.name != "nt":
        raise DataProtectionError("Windows DPAPI只能在Windows系统上使用")
    crypt32, kernel32 = _libraries()
    source, source_buffer = _blob(data)
    output = _DataBlob()
    description = wintypes.LPWSTR()
    succeeded = crypt32.CryptUnprotectData(
        ctypes.byref(source),
        ctypes.byref(description),
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    )
    _keep_alive(source_buffer)
    if not succeeded:
        raise DataProtectionError(ctypes.get_last_error(), "无法解密本机密钥")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        if output.pbData:
            kernel32.LocalFree(output.pbData)
        if description:
            kernel32.LocalFree(description)


def _crypt_protect(data: bytes, description: str) -> bytes:
    if os.name != "nt":
        raise DataProtectionError("Windows DPAPI只能在Windows系统上使用")
    crypt32, kernel32 = _libraries()
    source, source_buffer = _blob(data)
    output = _DataBlob()
    succeeded = crypt32.CryptProtectData(
        ctypes.byref(source),
        description,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    )
    _keep_alive(source_buffer)
    if not succeeded:
        raise DataProtectionError(ctypes.get_last_error(), "无法保护本机密钥")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        if output.pbData:
            kernel32.LocalFree(output.pbData)


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    return _DataBlob(len(data), pointer), buffer


def _libraries():
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    return crypt32, kernel32


def _keep_alive(_value) -> None:
    return None
