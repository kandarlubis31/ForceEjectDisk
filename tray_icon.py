"""Ikon system tray Windows dengan ctypes, tanpa dependensi eksternal.

Dipakai oleh force_eject.py. Message loop Win32 berjalan di thread sendiri,
jadi callback menu dipanggil dari thread itu - panggil balik ke Tk lewat
ForceEjectApp.post() / queue, jangan sentuh widget langsung.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from ctypes import wintypes

if sys.platform != "win32":
    raise RuntimeError("tray_icon hanya untuk Windows.")

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_APP = 0x8000

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010
NIIF_INFO = 0x00000001

IDI_APPLICATION = 32512
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
HWND_MESSAGE = -3
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100

CW_USEDEFAULT = -0x80000000


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wintypes.WORD
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
user32.AppendMenuW.restype = wintypes.BOOL
user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU,
    wintypes.UINT,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.LPVOID,
]
user32.TrackPopupMenu.restype = wintypes.BOOL
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.DestroyMenu.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
user32.LoadIconW.restype = wintypes.HICON
user32.LoadImageW.argtypes = [
    wintypes.HINSTANCE,
    wintypes.LPCWSTR,
    wintypes.UINT,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.LoadImageW.restype = wintypes.HANDLE
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.DestroyIcon.restype = wintypes.BOOL

shell32.ExtractIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT]
shell32.ExtractIconW.restype = wintypes.HICON

shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class TrayIcon:
    """Ikon tray dengan menu popup yang dibangun ulang setiap kali diklik.

    build_menu() harus mengembalikan list berisi tuple (label, callback) atau
    None untuk garis pemisah. Callback dijalankan di thread tray.
    """

    def __init__(
        self, tooltip: str, build_menu, on_activate=None, on_error=None, icon_path: str = ""
    ) -> None:
        self.tooltip = tooltip[:127]
        self.build_menu = build_menu
        self.on_activate = on_activate
        self.on_error = on_error or (lambda message: None)
        self.icon_path = icon_path

        self._hwnd = None
        self._icon = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._class_name = f"ForceEjectDiskTray_{id(self)}"
        self._wndproc_ref = WNDPROC(self._wndproc)  # simpan referensi agar tidak di-GC
        self._message = WM_APP + 1

    # ------------------------------------------------------------- lifecycle ---
    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, timeout: float = 5.0) -> bool:
        """Jalankan message loop tray di thread terpisah."""
        if self.running:
            return True
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self.running and self._hwnd is not None

    def stop(self, timeout: float = 3.0) -> None:
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout)
        self._thread = None

    def set_tooltip(self, tooltip: str) -> None:
        self.tooltip = tooltip[:127]
        if self._hwnd and self._icon:
            self._icon.szTip = self.tooltip
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._icon))

    def notify(self, title: str, text: str) -> None:
        """Tampilkan balon notifikasi di dekat ikon tray."""
        if not (self._hwnd and self._icon):
            return
        self._icon.uFlags = NIF_INFO
        self._icon.szInfoTitle = title[:63]
        self._icon.szInfo = text[:255]
        self._icon.dwInfoFlags = NIIF_INFO
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._icon))
        self._icon.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP

    # --------------------------------------------------------------- internal ---
    def _run(self) -> None:
        try:
            hinstance = kernel32.GetModuleHandleW(None)
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = ctypes.cast(self._wndproc_ref, ctypes.c_void_p)
            wc.hInstance = hinstance
            wc.lpszClassName = self._class_name
            if not user32.RegisterClassExW(ctypes.byref(wc)):
                raise ctypes.WinError(ctypes.get_last_error())

            hwnd = user32.CreateWindowExW(
                0,
                self._class_name,
                "ForceEjectDiskTray",
                0,
                0,
                0,
                0,
                0,
                wintypes.HWND(HWND_MESSAGE),
                None,
                hinstance,
                None,
            )
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            self._hwnd = hwnd

            self._icon = NOTIFYICONDATAW()
            self._icon.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            self._icon.hWnd = hwnd
            self._icon.uID = 1
            self._icon.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            self._icon.uCallbackMessage = self._message
            self._icon.szTip = self.tooltip
            self._icon.hIcon = self._load_icon(hinstance)
            if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._icon)):
                raise ctypes.WinError(ctypes.get_last_error())

            self._ready.set()
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            self.on_error(f"Tray gagal aktif: {exc}")
            self._ready.set()
        finally:
            self._hwnd = None

    def _load_icon(self, hinstance):
        """Ikon aplikasi: file .ico dulu, lalu ikon EXE, terakhir ikon Windows."""
        if self.icon_path and os.path.exists(self.icon_path):
            loaded = user32.LoadImageW(
                None,
                self.icon_path,
                IMAGE_ICON,
                0,
                0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE,
            )
            if loaded:
                return loaded
        if getattr(sys, "frozen", False):
            extracted = shell32.ExtractIconW(hinstance, sys.executable, 0)
            if extracted:
                return extracted
        return user32.LoadIconW(None, ctypes.c_wchar_p(IDI_APPLICATION))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == self._message:
            if lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._show_menu()
            elif lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK) and self.on_activate:
                self._run_guarded(self.on_activate)
            return 0
        if msg == WM_CLOSE:
            if self._icon:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._icon))
                self._icon = None
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self) -> None:
        if not self._hwnd:
            return
        try:
            items = self.build_menu()
        except Exception as exc:
            self.on_error(f"Gagal menyiapkan menu tray: {exc}")
            return

        menu = user32.CreatePopupMenu()
        if not menu:
            return
        callbacks = {}
        command = 1
        try:
            for item in items:
                if item is None:
                    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                    continue
                label, callback = item
                callbacks[command] = callback
                user32.AppendMenuW(menu, MF_STRING, command, label)
                command += 1

            point = POINT()
            user32.GetCursorPos(ctypes.byref(point))
            # wajib: tanpa ini menu tidak mau menutup saat klik di luar
            user32.SetForegroundWindow(self._hwnd)
            chosen = user32.TrackPopupMenu(
                menu,
                TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                point.x,
                point.y,
                0,
                self._hwnd,
                None,
            )
        finally:
            user32.DestroyMenu(menu)

        if chosen and chosen in callbacks:
            self._run_guarded(callbacks[chosen])

    def _run_guarded(self, callback) -> None:
        try:
            callback()
        except Exception as exc:
            self.on_error(f"Callback tray gagal: {exc}")
