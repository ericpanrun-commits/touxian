"""Windows file lease reused for scoped dispatch evidence and settings.

This proves neither human approval nor coordination of SQLite business data.
No running application entry point is enabled by importing this module.
An evidence change or revocation may be delayed until the lease is released;
callers must expose that state rather than claim an in-flight request was stopped.
"""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
from pathlib import Path


@contextmanager
def immutable_evidence(path,*,max_bytes=16384):
    """Deny new writes and replacement of one local file while reading it."""
    if os.name!='nt' or type(max_bytes) is not int or not 1<=max_bytes<=65536:
        raise OSError('approval_lease_unavailable')
    path=Path(path)
    if not path.is_absolute() or str(path).startswith(('\\\\','//')) or path.is_symlink():
        raise OSError('approval_lease_unavailable')
    try:path=path.resolve(strict=True)
    except OSError:raise OSError('approval_lease_unavailable') from None
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,
                                  wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.CloseHandle.restype=wintypes.BOOL
    kernel.GetFileSizeEx.argtypes=[wintypes.HANDLE,ctypes.POINTER(ctypes.c_longlong)]
    kernel.GetFileSizeEx.restype=wintypes.BOOL
    kernel.ReadFile.argtypes=[wintypes.HANDLE,wintypes.LPVOID,wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD),wintypes.LPVOID]
    kernel.ReadFile.restype=wintypes.BOOL
    class Attributes(ctypes.Structure):
        _fields_=[('attributes',wintypes.DWORD),('reparse_tag',wintypes.DWORD)]
    kernel.GetFileInformationByHandleEx.argtypes=[wintypes.HANDLE,ctypes.c_int,wintypes.LPVOID,wintypes.DWORD]
    kernel.GetFileInformationByHandleEx.restype=wintypes.BOOL
    handle=kernel.CreateFileW(str(path),0x80000000,0x00000001,None,3,0x00200080,None)
    if handle==ctypes.c_void_p(-1).value:
        raise OSError('approval_lease_unavailable')
    try:
        attributes=Attributes()
        if (not kernel.GetFileInformationByHandleEx(handle,9,ctypes.byref(attributes),ctypes.sizeof(attributes))
            or attributes.attributes & 0x00000400):
            raise OSError('approval_lease_unavailable')
        size=ctypes.c_longlong()
        if not kernel.GetFileSizeEx(handle,ctypes.byref(size)) or not 0<size.value<=max_bytes:
            raise OSError('approval_lease_unavailable')
        buffer=ctypes.create_string_buffer(size.value)
        read=wintypes.DWORD()
        if not kernel.ReadFile(handle,buffer,size.value,ctypes.byref(read),None) or read.value!=size.value:
            raise OSError('approval_lease_unavailable')
        yield buffer.raw[:read.value]
    finally:
        kernel.CloseHandle(handle)
