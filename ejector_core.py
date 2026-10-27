"""Core logic Force Eject Disk (Windows only).

Alur kerja:
1. Enumerasi drive logis (HDD eksternal sering terdeteksi sebagai FIXED).
2. Cari proses yang masih memegang handle File di drive itu dengan men-scan
   seluruh handle sistem (NtQuerySystemInformation + DuplicateHandle), sama
   seperti cara kerja Sysinternals handle.exe. Restart Manager tidak dipakai
   karena pendaftaran direktori tidak melaporkan file yang sedang terbuka.
3. Lepaskan drive, ada dua cara:
   - mode aman: tutup HANYA handle file ke drive itu (seperti Sysinternals
     handle.exe -c). Aplikasi yang memegangnya tetap terbuka.
   - mode paksa: hentikan proses penguncinya.
4. Lock volume -> dismount volume -> eject media (volume dan physical drive).
5. Kalau masih ada, minta PnP manager melepas device-nya.
"""

from __future__ import annotations

import ctypes
import os
import string
import tempfile
import threading
import time
from ctypes import wintypes

if os.name != "nt":
    raise RuntimeError("Force Eject Disk hanya bisa jalan di Windows.")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
cfgmgr32 = ctypes.WinDLL("cfgmgr32", use_last_error=True)

# ---------------------------------------------------------------- konstanta ---
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3

FSCTL_LOCK_VOLUME = 0x00090018
FSCTL_DISMOUNT_VOLUME = 0x00090020
IOCTL_STORAGE_MEDIA_REMOVAL = 0x002D4804
IOCTL_STORAGE_EJECT_MEDIA = 0x002D4808
IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x2D1080

DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010

ERROR_ACCESS_DENIED = 5
ERROR_NOT_READY = 21
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
SystemExtendedHandleInformation = 64

BUSY_ERRORS = {ERROR_ACCESS_DENIED, ERROR_NOT_READY, ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION}

# Batas waktu langkah yang bisa menggantung di driver (drive USB nyangkut).
VOLUME_STEP_TIMEOUT = 25.0
PRESENCE_TIMEOUT = 10.0

PROCESS_DUP_HANDLE = 0x0040
DUPLICATE_CLOSE_SOURCE = 0x00000001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
DUPLICATE_SAME_ACCESS = 0x00000002

FILE_NAME_NORMALIZED = 0x0
VOLUME_NAME_DOS = 0x0
VOLUME_NAME_NT = 0x2
FILE_TYPE_DISK = 0x0001

DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

DRIVE_TYPE_NAME = {
    DRIVE_UNKNOWN: "Tidak dikenal",
    DRIVE_NO_ROOT_DIR: "Tidak ada root",
    DRIVE_REMOVABLE: "Removable",
    DRIVE_FIXED: "Fixed / eksternal",
    DRIVE_REMOTE: "Network",
    DRIVE_CDROM: "CD/DVD",
    DRIVE_RAMDISK: "RAM disk",
}

# Proses inti Windows yang handle-nya jangan dilepas paksa, bahkan di mode
# aman: sistem bisa kacau kalau handle vitalnya dicabut. Kalau salah satu dari
# proses ini yang menahan drive, mode aman tidak bisa menolong.
HANDLE_PROTECTED_PROCESSES = {
    "system",
    "registry",
    "memory compression",
    "secure system",
    "system idle process",
    "idle",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
}

# Proses yang jangan dibunuh walau opsi "paksa tutup" aktif.
PROTECTED_PROCESSES = {
    "system",
    "system idle process",
    "idle",
    "registry",
    "memory compression",
    "secure system",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "dwm.exe",
    "fontdrvhost.exe",
    "audiodg.exe",
    "sihost.exe",
    "explorer.exe",
}

# ------------------------------------------------------------- binding API ----
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.DeviceIoControl.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
kernel32.DeviceIoControl.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.GetCurrentProcess.restype = ctypes.c_void_p
kernel32.DuplicateHandle.argtypes = [
    wintypes.HANDLE,
    wintypes.HANDLE,
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.DWORD,
    wintypes.BOOL,
    wintypes.DWORD,
]
kernel32.DuplicateHandle.restype = wintypes.BOOL
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateProcess.restype = wintypes.BOOL
kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
kernel32.FlushFileBuffers.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.GetFinalPathNameByHandleW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
]
kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
kernel32.QueryDosDeviceW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
kernel32.QueryDosDeviceW.restype = wintypes.DWORD
kernel32.GetFileType.argtypes = [wintypes.HANDLE]
kernel32.GetFileType.restype = wintypes.DWORD
kernel32.GetLogicalDrives.restype = wintypes.DWORD
kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
kernel32.GetDriveTypeW.restype = wintypes.UINT
kernel32.GetDiskFreeSpaceExW.argtypes = [
    wintypes.LPCWSTR,
    ctypes.POINTER(ctypes.c_ulonglong),
    ctypes.POINTER(ctypes.c_ulonglong),
    ctypes.POINTER(ctypes.c_ulonglong),
]
kernel32.GetDiskFreeSpaceExW.restype = wintypes.BOOL
kernel32.GetVolumeInformationW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPWSTR,
    wintypes.DWORD,
]
kernel32.GetVolumeInformationW.restype = wintypes.BOOL

ntdll.NtQuerySystemInformation.argtypes = [
    wintypes.ULONG,
    ctypes.c_void_p,
    wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
]
ntdll.NtQuerySystemInformation.restype = ctypes.c_long


class SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX(ctypes.Structure):
    _fields_ = [
        ("Object", ctypes.c_void_p),
        ("UniqueProcessId", ctypes.c_void_p),
        ("HandleValue", ctypes.c_void_p),
        ("GrantedAccess", wintypes.ULONG),
        ("CreatorBackTraceIndex", wintypes.USHORT),
        ("ObjectTypeIndex", wintypes.USHORT),
        ("HandleAttributes", wintypes.ULONG),
        ("Reserved", wintypes.ULONG),
    ]


class SYSTEM_HANDLE_INFORMATION_EX(ctypes.Structure):
    _fields_ = [
        ("NumberOfHandles", ctypes.c_void_p),
        ("Reserved", ctypes.c_void_p),
        ("Handles", SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX * 1),
    ]


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


GUID_DEVINTERFACE_DISK = _GUID(
    0x53F56307,
    0xB6BF,
    0x11D0,
    (ctypes.c_ubyte * 8)(0x94, 0xF2, 0x00, 0xA0, 0xC9, 0x1E, 0xFB, 0x8B),
)


class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", _GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(wintypes.ULONG)),
    ]


class _SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", _GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(wintypes.ULONG)),
    ]


class _SP_DEVICE_INTERFACE_DETAIL_DATA_W(ctypes.Structure):
    # DevicePath sebenarnya panjang; struct ini cuma dipakai untuk ukuran & offset.
    # Baca string-nya dengan wstring_at(alamat buffer + offset DevicePath).
    _fields_ = [("cbSize", wintypes.DWORD), ("DevicePath", wintypes.WCHAR * 1)]


def _detail_device_path(buffer) -> str:
    offset = ctypes.sizeof(wintypes.DWORD)
    return ctypes.wstring_at(ctypes.addressof(buffer) + offset)


class STORAGE_DEVICE_NUMBER(ctypes.Structure):
    _fields_ = [
        ("DeviceType", wintypes.DWORD),
        ("DeviceNumber", wintypes.DWORD),
        ("PartitionNumber", wintypes.DWORD),
    ]


class PREVENT_MEDIA_REMOVAL(ctypes.Structure):
    _fields_ = [("PreventMediaRemoval", ctypes.c_ubyte)]


setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(_GUID),
    wintypes.LPCWSTR,
    wintypes.HWND,
    wintypes.DWORD,
]
setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(_SP_DEVINFO_DATA),
    ctypes.POINTER(_GUID),
    wintypes.DWORD,
    ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
]
setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
    ctypes.POINTER(_SP_DEVICE_INTERFACE_DETAIL_DATA_W),
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(_SP_DEVINFO_DATA),
]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

cfgmgr32.CM_Request_Device_EjectW.argtypes = [
    wintypes.DWORD,
    ctypes.POINTER(ctypes.c_int),
    wintypes.LPWSTR,
    wintypes.ULONG,
    wintypes.ULONG,
]
cfgmgr32.CM_Request_Device_EjectW.restype = wintypes.DWORD
cfgmgr32.CM_Get_Parent.argtypes = [
    ctypes.POINTER(wintypes.DWORD),
    wintypes.DWORD,
    wintypes.ULONG,
]
cfgmgr32.CM_Get_Parent.restype = wintypes.DWORD
cfgmgr32.CM_Get_Device_IDW.argtypes = [
    wintypes.DWORD,
    wintypes.LPWSTR,
    wintypes.ULONG,
    wintypes.ULONG,
]
cfgmgr32.CM_Get_Device_IDW.restype = wintypes.DWORD


# ------------------------------------------------------------------ helpers ---
def is_admin() -> bool:
    """True kalau proses berjalan dengan hak administrator."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def normalize_letter(drive: str) -> str:
    """'x:\\', 'X:', 'x' -> 'X'."""
    letter = str(drive).strip().strip("\\/").rstrip(":").strip().strip("\\/")
    if len(letter) != 1 or letter.upper() not in string.ascii_uppercase:
        raise ValueError(f"Drive tidak valid: {drive!r}")
    return letter.upper()


def _valid_handle(handle) -> bool:
    return bool(handle) and handle != INVALID_HANDLE_VALUE


def _drive_size(root: str):
    free = ctypes.c_ulonglong(0)
    total = ctypes.c_ulonglong(0)
    if not kernel32.GetDiskFreeSpaceExW(root, ctypes.byref(free), ctypes.byref(total), None):
        return None
    return total.value, free.value


def _drive_label(root: str):
    name_buf = ctypes.create_unicode_buffer(261)
    fs_buf = ctypes.create_unicode_buffer(261)
    serial = wintypes.DWORD(0)
    max_len = wintypes.DWORD(0)
    flags = wintypes.DWORD(0)
    ok = kernel32.GetVolumeInformationW(
        root,
        name_buf,
        len(name_buf),
        ctypes.byref(serial),
        ctypes.byref(max_len),
        ctypes.byref(flags),
        fs_buf,
        len(fs_buf),
    )
    if not ok:
        return "", ""
    return name_buf.value, fs_buf.value


def system_drive_letter() -> str:
    return os.environ.get("SystemRoot", "C:\\Windows")[:1].upper()


def _device_number_for_letter(letter: str):
    r"""Nomor physical drive (\\.\PhysicalDriveN) untuk sebuah huruf drive."""
    probe = _open_device(f"\\\\.\\{letter}:", access=0, retries=1)
    if probe is None:
        return None
    try:
        return _volume_device_number(probe)
    finally:
        kernel32.CloseHandle(probe)


def _disk_bus(device_number: int) -> str:
    """'USB' kalau disk nempel lewat USB, 'internal' kalau tidak, '' kalau tak pasti.

    Disk di enclosure USB sering muncul sebagai "SCSI Disk Device" (lewat UASP),
    jadi yang menentukan adalah devnode induknya, bukan nama/kelas disk-nya.
    """
    devnode = _find_disk_devnode(device_number)
    if devnode is None:
        return ""
    current = devnode
    for _ in range(6):
        parent = wintypes.DWORD(0)
        if cfgmgr32.CM_Get_Parent(ctypes.byref(parent), current, 0) != 0:
            break
        buffer = ctypes.create_unicode_buffer(512)
        if cfgmgr32.CM_Get_Device_IDW(parent.value, buffer, len(buffer), 0) != 0:
            break
        if buffer.value.upper().startswith("USB\\"):
            return "USB"
        current = parent.value
    return "internal"


def drive_bus(drive: str) -> str:
    """'USB', 'internal', atau '' kalau tidak bisa dipastikan."""
    letter = normalize_letter(drive)
    if letter == system_drive_letter():
        return "internal"
    number = _device_number_for_letter(letter)
    if number is None:
        return ""
    return _disk_bus(number)


def list_drives() -> list[dict]:
    """Daftar drive yang terpasang beserta label, tipe, dan kapasitas."""
    mask = kernel32.GetLogicalDrives()
    drives = []
    for index, letter in enumerate(string.ascii_uppercase):
        if not (mask >> index) & 1:
            continue
        root = f"{letter}:\\"
        dtype = kernel32.GetDriveTypeW(root)
        if dtype in (DRIVE_UNKNOWN, DRIVE_NO_ROOT_DIR, DRIVE_REMOTE):
            continue
        label, fs = _drive_label(root)
        size = _drive_size(root)
        drives.append(
            {
                "letter": letter,
                "root": root,
                "label": label,
                "fs": fs,
                "type": DRIVE_TYPE_NAME.get(dtype, "?"),
                "drive_type": dtype,
                "total": size[0] if size else 0,
                "free": size[1] if size else 0,
                "removable": dtype == DRIVE_REMOVABLE,
                "system": letter == system_drive_letter(),
                "bus": drive_bus(letter),
                "present": os.path.exists(root),
            }
        )
    return drives


def is_drive_present(drive: str) -> bool:
    """Cek drive masih terpasang (mount point masih ada)."""
    letter = normalize_letter(drive)
    if not (kernel32.GetLogicalDrives() >> (ord(letter) - 65)) & 1:
        return False
    return bool(os.path.exists(f"{letter}:\\"))


_name_cache: dict[int, str] = {}


def _process_exe(pid: int) -> str:
    if pid in _name_cache:
        return _name_cache[pid]
    exe = ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if _valid_handle(handle):
        try:
            size = wintypes.DWORD(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                exe = buf.value
        finally:
            kernel32.CloseHandle(handle)
    _name_cache[pid] = exe
    return exe


def _final_path(handle, flags: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    needed = kernel32.GetFinalPathNameByHandleW(handle, buf, len(buf), flags)
    if needed == 0:
        return ""
    if needed > len(buf):
        buf = ctypes.create_unicode_buffer(needed + 2)
        needed = kernel32.GetFinalPathNameByHandleW(handle, buf, len(buf), flags)
        if needed == 0:
            return ""
    path = buf.value
    # hasil VOLUME_NAME_DOS selalu berbentuk \\?\C:\...
    if path.startswith("\\\\?\\"):
        path = path[4:]
    return path


# ------------------------------------------------- deteksi proses pengunci ---
def _holds_target_volume(handle, target_volume) -> bool:
    """True kalau handle ini memegang volume/disk target secara langsung."""
    if target_volume is None:
        return False
    info = _storage_device_number(handle)
    if info is None:
        return False
    if info.DeviceNumber != target_volume.DeviceNumber:
        return False
    # PartitionNumber 0 = handle ke physical drive, bukan ke satu partisi
    return info.PartitionNumber in (target_volume.PartitionNumber, 0)


def _query_system_handles():
    """Ambil snapshot seluruh handle sistem.

    Return (buffer, entries, count). Buffer wajib tetap hidup selama entries
    dipakai; kalau buffer dibebaskan, entries jadi pointer menggantung
    (segfault) karena struct-nya tidak dikopi saat cast.
    """
    size = 1 << 24
    while size <= (1 << 29):
        buf = ctypes.create_string_buffer(size)
        needed = wintypes.ULONG(0)
        status = ntdll.NtQuerySystemInformation(
            SystemExtendedHandleInformation, buf, size, ctypes.byref(needed)
        )
        if status == 0:
            info = ctypes.cast(buf, ctypes.POINTER(SYSTEM_HANDLE_INFORMATION_EX))
            count = int(info.contents.NumberOfHandles or 0)
            offset = 2 * ctypes.sizeof(ctypes.c_void_p)
            entries = ctypes.cast(
                ctypes.addressof(buf) + offset,
                ctypes.POINTER(SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX),
            )
            return buf, entries, count
        if status != STATUS_INFO_LENGTH_MISMATCH:
            return None, None, 0
        size *= 2
    return None, None, 0


_file_type_index: int | None = None


def _file_object_type_index() -> int | None:
    """Cari ObjectTypeIndex untuk objek tipe 'File' (~ 1x per proses)."""
    global _file_type_index
    if _file_type_index is not None:
        return _file_type_index
    path = os.path.join(tempfile.gettempdir(), f"_fe_type_probe_{os.getpid()}.tmp")
    with open(path, "w") as handle_file:
        handle_file.write("x")
    probe = kernel32.CreateFileW(
        path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None
    )
    if not _valid_handle(probe):
        return None
    try:
        _buf, entries, count = _query_system_handles()
        if entries is None:
            return None
        me = os.getpid()
        wanted = int(probe)
        for index in range(count):
            entry = entries[index]
            if entry.UniqueProcessId == me and entry.HandleValue == wanted:
                _file_type_index = entry.ObjectTypeIndex
                return _file_type_index
    finally:
        kernel32.CloseHandle(probe)
        try:
            os.remove(path)
        except OSError:
            pass
    return None


def _walk_drive_handles(drive: str, visitor) -> int:
    """Telusuri handle File milik proses lain yang menunjuk ke drive target.

    visitor(pid, proc_handle, handle_value, duplicate, path) dipanggil untuk
    setiap handle yang cocok. `duplicate` adalah salinan handle itu di proses
    ini dengan akses yang sama; salinan tersebut ditutup otomatis setelah
    visitor selesai, jadi jangan disimpan atau ditutup sendiri.

    Return jumlah proses yang tidak bisa diperiksa (biasanya butuh Admin).
    """
    letter = normalize_letter(drive)
    local_prefix = f"{letter}:\\"
    nt_prefix = ""
    dos_buf = ctypes.create_unicode_buffer(1024)
    if kernel32.QueryDosDeviceW(f"{letter}:", dos_buf, len(dos_buf)):
        nt_prefix = dos_buf.value + "\\"

    # Info volume target: dipakai untuk mendeteksi proses yang memegang handle
    # langsung ke volume/disk (\\.\X: atau \\.\PhysicalDriveN), karena handle
    # seperti itu tidak punya path file yang bisa dibaca.
    target_volume = None
    probe = _open_device(f"\\\\.\\{letter}:", access=0, retries=1)
    if probe is not None:
        try:
            target_volume = _storage_device_number(probe)
        finally:
            kernel32.CloseHandle(probe)

    type_index = _file_object_type_index()
    snapshot, entries, count = _query_system_handles()
    if entries is None:
        raise OSError("Tidak bisa membaca daftar handle sistem.")

    me = os.getpid()
    handle_cache: dict[int, object | None] = {}
    inaccessible: set[int] = set()

    for index in range(count):
        entry = entries[index]
        if type_index is not None and entry.ObjectTypeIndex != type_index:
            continue
        pid = entry.UniqueProcessId
        if not pid or pid in (me, 4):
            continue
        if pid not in handle_cache:
            proc = kernel32.OpenProcess(PROCESS_DUP_HANDLE, False, pid)
            handle_cache[pid] = proc if _valid_handle(proc) else None
        proc = handle_cache[pid]
        if proc is None:
            inaccessible.add(pid)
            continue

        duplicated = wintypes.HANDLE()
        if not kernel32.DuplicateHandle(
            proc,
            entry.HandleValue,
            kernel32.GetCurrentProcess(),
            ctypes.byref(duplicated),
            0,
            False,
            DUPLICATE_SAME_ACCESS,
        ):
            continue
        try:
            # Hanya handle bertipe DISK yang punya path. Handle pipe/device
            # (mis. control device driver) bisa menggantungkan pemanggilan
            # GetFinalPathNameByHandleW, jadi harus disaring lebih dulu.
            if kernel32.GetFileType(duplicated) != FILE_TYPE_DISK:
                continue
            path = _final_path(duplicated, FILE_NAME_NORMALIZED | VOLUME_NAME_DOS)
            if not (path and path.upper().startswith(local_prefix)):
                nt_path = _final_path(duplicated, VOLUME_NAME_NT)
                if nt_prefix and nt_path.upper().startswith(nt_prefix.upper()):
                    path = nt_path
                elif _holds_target_volume(duplicated, target_volume):
                    path = f"(handle langsung ke volume {letter}: / disk fisiknya)"
                else:
                    continue
            visitor(pid, proc, entry.HandleValue, duplicated, path)
        finally:
            kernel32.CloseHandle(duplicated)

    for proc in handle_cache.values():
        if proc:
            kernel32.CloseHandle(proc)
    del snapshot
    return len(inaccessible)


def scan_drive(drive: str) -> tuple[list[dict], int]:
    """Cari proses yang masih memegang file/direktori di drive tersebut.

    Return (daftar pengunci, jumlah proses yang tidak bisa diperiksa).
    Setiap pengunci: {pid, name, exe, paths, protected}.
    """
    found: dict[int, dict] = {}

    def visitor(pid, proc, handle_value, duplicate, path):
        record = found.get(pid)
        if record is None:
            exe = _process_exe(pid)
            name = os.path.basename(exe) or f"PID {pid}"
            record = {
                "pid": pid,
                "name": name,
                "exe": exe,
                "paths": [],
                "protected": name.lower() in PROTECTED_PROCESSES,
            }
            found[pid] = record
        if len(record["paths"]) < 3 and path not in record["paths"]:
            record["paths"].append(path)

    hidden = _walk_drive_handles(drive, visitor)
    lockers = sorted(found.values(), key=lambda item: item["name"].lower())
    return lockers, hidden


def find_lockers(drive: str) -> list[dict]:
    """Daftar proses yang masih memegang file di drive tersebut."""
    return scan_drive(drive)[0]


# ----------------------------------------------------------- paksa tutup ------
def terminate_process(pid: int) -> tuple[bool, str]:
    handle = kernel32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, pid)
    if not _valid_handle(handle):
        return False, f"tidak bisa membuka proses (error {ctypes.get_last_error()})"
    try:
        if not kernel32.TerminateProcess(handle, 1):
            return False, f"TerminateProcess gagal (error {ctypes.get_last_error()})"
        kernel32.WaitForSingleObject(handle, 3000)
        return True, "dihentikan"
    finally:
        kernel32.CloseHandle(handle)


def close_lockers(drive: str, log=None) -> list[dict]:
    """Paksa tutup semua proses pengunci (kecuali proses terproteksi)."""
    log = log or (lambda *_: None)
    remaining = []
    for item in find_lockers(drive):
        if item["protected"]:
            remaining.append(item)
            log(f"  ! dilewati: {item['name']} (PID {item['pid']}) - proses sistem")
            continue
        ok, message = terminate_process(item["pid"])
        if ok:
            log(f"  - ditutup: {item['name']} (PID {item['pid']})")
        else:
            log(f"  ! gagal paksa {item['name']} (PID {item['pid']}): {message}")
            remaining.append(item)
    if remaining:
        time.sleep(1.0)
        alive = {item["pid"] for item in find_lockers(drive)}
        remaining = [item for item in remaining if item["pid"] in alive]
    return remaining


# ------------------------------------------------ mode aman: lepas handle -----
def _close_remote_handle(proc, handle_value) -> bool:
    """Tutup satu handle milik proses lain tanpa mematikan prosesnya.

    DuplicateHandle dengan DUPLICATE_CLOSE_SOURCE menutup handle asal di proses
    pemiliknya (begitu cara kerja `handle.exe -c`); salinan di proses ini
    langsung ditutup lagi supaya objeknya benar-benar dilepas.
    """
    target = wintypes.HANDLE()
    ok = kernel32.DuplicateHandle(
        proc,
        handle_value,
        kernel32.GetCurrentProcess(),
        ctypes.byref(target),
        0,
        False,
        DUPLICATE_SAME_ACCESS | DUPLICATE_CLOSE_SOURCE,
    )
    if ok and _valid_handle(target.value):
        kernel32.CloseHandle(target)
        return True
    return False


def release_drive_handles(drive: str, log=None, flush: bool = True) -> dict:
    """Mode aman: lepas drive tanpa mematikan aplikasi yang memakainya.

    Hanya handle file ke drive itu yang ditutup, jadi aplikasi (mis. VSCode)
    tetap terbuka - yang hilang cuma aksesnya ke drive tersebut. Isi buffer file
    di-flush lebih dulu supaya tulisan yang belum sampai ke disk tidak hilang,
    tapi dokumen yang belum di-save tetap tanggung jawab aplikasinya: tool ini
    tidak bisa menekan Ctrl+S di aplikasi orang lain.

    Return {'closed', 'failed', 'flushed', 'skipped', 'processes',
    'inaccessible'}; 'processes' berisi ringkasan per proses.
    """
    log = log or (lambda *_: None)
    stats = {
        "closed": 0,
        "failed": 0,
        "flushed": 0,
        "skipped": [],
        "processes": {},
        "inaccessible": 0,
    }

    def visitor(pid, proc, handle_value, duplicate, path):
        exe = _process_exe(pid)
        name = os.path.basename(exe) or f"PID {pid}"
        if name.lower() in HANDLE_PROTECTED_PROCESSES:
            stats["skipped"].append({"pid": pid, "name": name})
            return
        record = stats["processes"].setdefault(
            pid, {"pid": pid, "name": name, "closed": 0, "failed": 0, "paths": []}
        )
        if len(record["paths"]) < 3 and path not in record["paths"]:
            record["paths"].append(path)
        if flush and kernel32.FlushFileBuffers(duplicate):
            stats["flushed"] += 1
        if _close_remote_handle(proc, handle_value):
            stats["closed"] += 1
            record["closed"] += 1
        else:
            stats["failed"] += 1
            record["failed"] += 1

    stats["inaccessible"] = _walk_drive_handles(drive, visitor)

    for record in sorted(stats["processes"].values(), key=lambda item: item["name"].lower()):
        if record["failed"]:
            log(
                f"  - {record['name']} (PID {record['pid']}) tetap jalan: "
                f"{record['closed']} handle dilepas, {record['failed']} gagal"
            )
        else:
            log(
                f"  - {record['name']} (PID {record['pid']}) tetap jalan: "
                f"{record['closed']} handle dilepas"
            )
        for path in record["paths"]:
            log(f"      {path}")
    for item in stats["skipped"]:
        log(f"  ! dilewati: {item['name']} (PID {item['pid']}) - proses inti Windows")
    return stats


# ------------------------------------------------------ buka handle device ---
def _open_device(path: str, access: int = GENERIC_READ | GENERIC_WRITE, retries: int = 4):
    for attempt in range(retries):
        handle = kernel32.CreateFileW(
            path, access, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None
        )
        if _valid_handle(handle):
            return handle
        error = ctypes.get_last_error()
        if error in BUSY_ERRORS and attempt < retries - 1:
            time.sleep(0.5)
            continue
        if access != 0:
            return _open_device(path, 0, 1)
        return None
    return None


def _ioctl(handle, code: int, in_struct=None, out_size: int = 0):
    returned = wintypes.DWORD(0)
    in_ptr = ctypes.byref(in_struct) if in_struct is not None else None
    in_size = ctypes.sizeof(in_struct) if in_struct is not None else 0
    out_buf = ctypes.create_string_buffer(out_size) if out_size else None
    ok = kernel32.DeviceIoControl(
        handle, code, in_ptr, in_size, out_buf, out_size, ctypes.byref(returned), None
    )
    return bool(ok), out_buf


def _storage_device_number(handle):
    """Ambil (DeviceType, DeviceNumber, PartitionNumber) dari handle volume/disk."""
    ok, buf = _ioctl(
        handle, IOCTL_STORAGE_GET_DEVICE_NUMBER, None, ctypes.sizeof(STORAGE_DEVICE_NUMBER)
    )
    if not ok or buf is None:
        return None
    return STORAGE_DEVICE_NUMBER.from_buffer_copy(buf.raw)


def _volume_device_number(handle):
    info = _storage_device_number(handle)
    return info.DeviceNumber if info else None


def _eject_volume(letter: str, log, eject_media: bool = False) -> bool:
    path = f"\\\\.\\{letter}:"
    # tiap langkah dicatat SEBELUM dipanggil: kalau driver menggantung, baris
    # terakhir di log menunjukkan persis panggilan mana yang nyangkut.
    log(f"  - membuka volume {path}...")
    handle = _open_device(path)
    if handle is None:
        log(f"  ! tidak bisa membuka volume {path} (error {ctypes.get_last_error()})")
        if not is_admin():
            log("    -> jalankan tool sebagai Administrator")
        return False
    try:
        log("  - mengizinkan pelepasan media...")
        _ioctl(handle, IOCTL_STORAGE_MEDIA_REMOVAL, PREVENT_MEDIA_REMOVAL(0))

        log("  - lock volume...")
        if not _ioctl(handle, FSCTL_LOCK_VOLUME)[0]:
            log(f"  ! lock volume gagal (error {ctypes.get_last_error()})")
            return False
        log("  - volume berhasil di-lock")
        log("  - dismount volume...")

        if _ioctl(handle, FSCTL_DISMOUNT_VOLUME)[0]:
            log("  - volume di-dismount")
        else:
            log(f"  ! dismount volume gagal (error {ctypes.get_last_error()})")

        if eject_media:
            log("  - kirim perintah eject media ke hardware...")
            if _ioctl(handle, IOCTL_STORAGE_EJECT_MEDIA)[0]:
                log("  - perintah eject media dikirim")
            else:
                log(f"  ! eject media gagal (error {ctypes.get_last_error()})")
        return True
    finally:
        kernel32.CloseHandle(handle)


def _find_disk_devnode(device_number: int):
    """Cari DEVINST devnode disk dengan nomor physical drive tertentu."""
    hdevinfo = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(GUID_DEVINTERFACE_DISK), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if not _valid_handle(hdevinfo):
        return None
    try:
        index = 0
        while True:
            iface = _SP_DEVICE_INTERFACE_DATA()
            iface.cbSize = ctypes.sizeof(_SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                hdevinfo, None, ctypes.byref(GUID_DEVINTERFACE_DISK), index, ctypes.byref(iface)
            ):
                return None
            index += 1

            required = wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdevinfo, ctypes.byref(iface), None, 0, ctypes.byref(required), None
            )
            if required.value == 0:
                continue

            buffer = ctypes.create_string_buffer(required.value)
            detail = ctypes.cast(buffer, ctypes.POINTER(_SP_DEVICE_INTERFACE_DETAIL_DATA_W))
            detail.contents.cbSize = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            devinfo = _SP_DEVINFO_DATA()
            devinfo.cbSize = ctypes.sizeof(_SP_DEVINFO_DATA)
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdevinfo,
                ctypes.byref(iface),
                detail,
                required.value,
                ctypes.byref(required),
                ctypes.byref(devinfo),
            ):
                continue

            device_path = _detail_device_path(buffer)
            handle = _open_device(device_path, access=GENERIC_READ, retries=1)
            if handle is None:
                continue
            try:
                if _volume_device_number(handle) == device_number:
                    return devinfo.DevInst
            finally:
                kernel32.CloseHandle(handle)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdevinfo)
    return None


def _with_timeout(action, seconds: float, log, label: str) -> bool:
    """Jalankan action di thread terpisah dengan batas waktu.

    Panggilan driver yang menggantung (mis. drive USB yang jembatannya nyangkut)
    tidak bisa dibatalkan dari luar, jadi kita berhenti menunggu saja dan lanjut
    ke langkah berikutnya. Tanpa ini, aplikasi bisa nunggu tanpa batas.
    """
    thread = threading.Thread(target=action, daemon=True, name=f"fe-{label}")
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        log(f"  ! {label} tidak merespons setelah {seconds:.0f} detik - dilewati")
        return False
    return True


def _drive_present_with_timeout(letter: str, seconds: float, log) -> bool:
    """is_drive_present() dengan batas waktu; kalau menggantung dianggap masih ada."""
    result: dict[str, bool] = {}

    def check() -> None:
        try:
            result["value"] = is_drive_present(letter)
        except Exception:
            result["value"] = True

    thread = threading.Thread(target=check, daemon=True, name="fe-present")
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        log(
            f"  ! cek status {letter}: tidak merespons setelah {seconds:.0f} detik "
            "- dianggap masih terpasang"
        )
        return True
    return result.get("value", True)


def _eject_physical(letter: str, log, eject_media: bool = False) -> bool:
    """Lock + lepas physical drive, lalu minta PnP manager melepas device-nya.

    eject_media=True menambah IOCTL_STORAGE_EJECT_MEDIA ke device. Itu perintah
    tingkat hardware (drive diputar-turun/dimedia-eject); jembatan USB-SATA bisa
    nyangkut dan bikin drive berputar naik-turun berulang, jadi default-nya mati.
    """
    handle = _open_device(f"\\\\.\\{letter}:")
    if handle is None:
        return False
    try:
        device_number = _volume_device_number(handle)
    finally:
        kernel32.CloseHandle(handle)
    if device_number is None:
        log("  ! nomor physical drive tidak terdeteksi, lewati eject device")
        return False

    ejected = False
    physical = _open_device(f"\\\\.\\PhysicalDrive{device_number}")
    if physical is not None:
        try:
            _ioctl(physical, FSCTL_LOCK_VOLUME)
            _ioctl(physical, FSCTL_DISMOUNT_VOLUME)
            if eject_media:
                _ioctl(physical, IOCTL_STORAGE_MEDIA_REMOVAL, PREVENT_MEDIA_REMOVAL(0))
                if _ioctl(physical, IOCTL_STORAGE_EJECT_MEDIA)[0]:
                    log(f"  - perintah eject PhysicalDrive{device_number} dikirim")
                    ejected = True
                else:
                    log(
                        f"  ! eject PhysicalDrive{device_number} gagal "
                        f"(error {ctypes.get_last_error()})"
                    )
        finally:
            kernel32.CloseHandle(physical)
    else:
        log(f"  ! tidak bisa membuka \\\\.\\PhysicalDrive{device_number}")

    devnode = _find_disk_devnode(device_number)
    if devnode is None:
        log("  ! devnode disk tidak ditemukan, lewati permintaan lepas device")
        return ejected

    veto_type = ctypes.c_int(0)
    veto_name = ctypes.create_unicode_buffer(260)
    result = cfgmgr32.CM_Request_Device_EjectW(
        devnode, ctypes.byref(veto_type), veto_name, len(veto_name), 0
    )
    if result == 0:
        log("  - device dilepas oleh PnP manager")
        return True
    log(f"  ! CM_Request_Device_Eject gagal (kode {result}, veto {veto_type.value}: {veto_name.value})")
    return ejected


# ------------------------------------------------------------------- publik ---
def force_eject(
    drive: str,
    kill_lockers: bool = True,
    log=None,
    safe_mode: bool = False,
    eject_media: bool = False,
    allow_internal: bool = False,
) -> bool:
    """Paksa eject drive.

    safe_mode=True -> mode aman: hanya handle file ke drive itu yang dilepas,
    aplikasi penguncinya tetap terbuka. safe_mode=False -> perilaku lama, di
    mana kill_lockers menentukan apakah proses pengunci dihentikan.
    eject_media=True -> ikut mengirim IOCTL_STORAGE_EJECT_MEDIA ke hardware.
    allow_internal=True -> izinkan mencoba disk internal (percuma: selalu ditolak).

    Return True kalau drive sudah lepas.
    """
    log = log or (lambda *_: None)
    letter = normalize_letter(drive)

    if not is_drive_present(letter):
        log(f"Drive {letter}: sudah tidak terpasang.")
        return True

    log(f"== Force eject {letter}: ==")
    bus = drive_bus(letter)
    if bus == "internal" and not allow_internal:
        log(f"  ! {letter}: ada di disk internal (SATA/NVMe), bukan drive yang bisa dicabut.")
        log("    Windows selalu menolak melepas disk internal, dan lock + dismount-nya")
        log("    bisa bikin sistem/rekaman di disk itu goyang. Dibatalkan.")
        return False
    if bus:
        log(f"  bus: {bus}")
    probe = _open_device(f"\\\\.\\{letter}:", access=0, retries=1)
    if probe is not None:
        try:
            info = _storage_device_number(probe)
            if info:
                log(
                    f"  target: PhysicalDrive{info.DeviceNumber} "
                    f"partisi {info.PartitionNumber} (tipe device {info.DeviceType})"
                )
        finally:
            kernel32.CloseHandle(probe)
    else:
        log(f"  ! tidak bisa membuka {letter}: untuk membaca info device")

    apps: list[dict] = []
    hidden = 0
    try:
        apps, hidden = scan_drive(letter)
    except OSError as exc:
        log(f"  ! gagal memindai handle sistem: {exc}")

    if apps:
        log(f"Ditemukan {len(apps)} proses masih memakai {letter}:")
        for item in apps:
            log(f"  * {item['name']} (PID {item['pid']})")
            for path in item["paths"]:
                log(f"      {path}")
    else:
        log("Tidak ada proses yang terdeteksi memegang file di drive ini.")
    if hidden:
        log(f"  ! {hidden} proses tidak bisa diperiksa (butuh Administrator).")

    if apps and safe_mode:
        log(f"Mode aman: lepas handle ke {letter}: tanpa menutup aplikasi...")
        try:
            stats = release_drive_handles(letter, log)
            still = find_lockers(letter)
        except OSError as exc:
            # pemindaian gagal bukan alasan untuk membatalkan eject: volume
            # masih mungkin bisa di-lock, jadi lanjutkan saja.
            log(f"  ! gagal memindai handle sistem: {exc}")
        else:
            log(
                f"  total: {stats['closed']} handle dilepas, {stats['flushed']} file di-flush, "
                f"{stats['failed']} gagal."
            )
            if not stats["closed"]:
                log("  ! tidak ada handle yang berhasil dilepas.")
            if still:
                log(f"  ! masih ada {len(still)} proses yang memegang {letter}: setelah handle dilepas:")
                for item in still:
                    log(f"      {item['name']} (PID {item['pid']})")
            else:
                log("  - tidak ada lagi proses yang memegang drive ini.")
    elif apps and kill_lockers:
        log("Menutup paksa proses pengunci...")
        remaining = close_lockers(letter, log)
        if remaining:
            log(f"Masih ada {len(remaining)} proses yang tidak bisa ditutup.")
    elif apps:
        log("Lewati penutupan paksa (opsi paksa tutup tidak aktif).")

    log("Eject volume...")
    _with_timeout(
        lambda: _eject_volume(letter, log, eject_media=eject_media),
        VOLUME_STEP_TIMEOUT,
        log,
        "lock/dismount volume",
    )

    if _drive_present_with_timeout(letter, PRESENCE_TIMEOUT, log):
        log("Drive masih terpasang, coba lepas device-nya lewat PnP manager...")
        _eject_physical(letter, log, eject_media=eject_media)

    time.sleep(0.8)
    if _drive_present_with_timeout(letter, PRESENCE_TIMEOUT, log):
        log(
            f"HASIL: {letter}: MASIH terpasang. Coba jalankan sebagai Administrator, "
            "atau tutup manual aplikasi yang memakai drive ini."
        )
        if safe_mode:
            log(
                "  Kalau masih ada proses yang menahan (lihat daftar di atas), biasanya "
                "file-nya di-map ke memori (dart/flutter, database, antivirus) atau "
                "dipegang proses inti Windows - itu hanya bisa dilepas dengan menutup "
                "aplikasinya, bukan dengan melepas handle."
            )
        if bus == "USB":
            log(
                "  Kalau drive USB-nya jadi bunyi berputar naik-turun berulang: cabut kabel"
                " USB-nya ~10 detik lalu colok lagi (jembatan USB-SATA-nya nyangkut)."
            )
        return False

    log(f"HASIL: {letter}: berhasil di-eject. Aman dicabut sekarang.")
    return True
