"""Force Eject Disk - GUI + tray (Windows).

Pilih drive, klik Force Eject. Tool akan mencari aplikasi latar belakang yang
masih memegang drive tersebut, menutupnya (opsional), lalu lock + dismount +
eject volume dan device-nya.

Aplikasi juga punya ikon di system tray dengan menu eject per drive, dan semua
log ditulis ke file supaya bisa dicek setelah aplikasi ditutup.
"""

from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import ejector_core as core
import tray_icon

APP_TITLE = "Force Eject Disk"
APP_VERSION = "1.0.0"
APP_AUTHOR = "Aulia Iskandar Lubis"
APP_LICENSE = "MIT"
DISK_CAPACITY_STEP = 1024
HELP_TEXT = {
    "pakai": """CARA PAKAI

1. Jalankan ForceEjectDisk.exe (klik Yes di UAC). Tool butuh Administrator
   supaya bisa lock/dismount volume dan melepas device-nya.
2. Klik Refresh kalau baru mencolok drive.
3. Pilih drive-nya di daftar. Lihat kolom Tipe:
     "USB / bisa dicabut" = drive eksternal, aman untuk dicabut
     "SATA / internal"    = disk dalam laptop/PC, TIDAK bisa dicabut
4. Pilih cara melepas di panel "Cara melepas drive":
     Mode aman  = hanya handle file ke drive itu yang dilepas. Aplikasi
                  (mis. VSCode) tetap terbuka, cuma aksesnya ke drive yang
                  hilang. Simpan dulu (Ctrl+S) sebelum klik.
     Mode paksa = proses yang menahan drive ditutup paksa. Data yang belum
                  disimpan bisa hilang.
5. Klik Eject aman / Eject paksa.
6. Kalau log bilang "berhasil di-eject", cabut drive-nya.

Berguna juga:
  - Cek Pengunci  : lihat aplikasi mana yang sedang memakai drive itu.
  - Klik dua kali pada baris drive = langsung eject.
  - Ikon tray    : klik kanan untuk menu cepat (eject aman/paksa per drive).
  - Tutup jendela : aplikasi pindah ke tray, masih bisa dipakai dari situ.
""",
    "kerja": """CARA KERJA

1. Mencari pengunci. Seluruh handle sistem dibaca via NtQuerySystemInformation,
   tiap handle diduplikasi ke proses ini, difilter dengan GetFileType, lalu
   path-nya dibaca dengan GetFinalPathNameByHandleW. Hasilnya: daftar aplikasi
   yang benar-benar memakai drive itu, lengkap dengan file-nya. Proses yang
   memegang handle langsung ke volume (mis. \\.\\X:) juga dikenali lewat
   IOCTL_STORAGE_GET_DEVICE_NUMBER.
2. Melepas drive:
     Mode aman  : handle file ke drive itu ditutup di proses pemiliknya
                  (DuplicateHandle + DUPLICATE_CLOSE_SOURCE) setelah isi
                  buffernya di-flush. Prosesnya tidak dibunuh.
     Mode paksa : proses penguncinya dihentikan (proses inti Windows dilewati).
3. Menutup volume: FSCTL_LOCK_VOLUME lalu FSCTL_DISMOUNT_VOLUME pada \\.\\X:.
4. Melepas device: kalau drive masih terpasang, \\.\\PhysicalDriveN dikunci +
   di-dismount, lalu PnP manager diminta melepas devnode disk-nya dengan
   CM_Request_Device_EjectW. Sama seperti "Safely Remove Hardware" bawaan
   Windows.

BATASAN

- Windows saja.
- Disk internal ditolak. Windows selalu menolak melepas disk internal, dan
  lock + dismount-nya bisa membuat sistem atau disk itu goyah.
- Mode aman tidak bisa melepas file yang di-map ke memori. Ini sering terjadi
  pada aplikasi yang sedang jalan dari drive itu: flutter run/Dart VM, database
  terbuka, antivirus yang sedang memeriksa. Untuk kasus itu prosesnya perlu
  dihentikan dulu (mode paksa).
- Mode aman juga tidak bisa menyimpan dokumen yang belum di-save. Flush hanya
  menjamin data yang sudah diserahkan aplikasi ke Windows benar-benar ke disk.
- Perintah eject media ke hardware (IOCTL_STORAGE_EJECT_MEDIA) sengaja tidak
  dikirim kecuali diminta lewat centang. Perintah itu bisa membuat jembatan
  USB-SATA nyangkut dan drive berputar naik-turun berulang. Kalau itu terjadi:
  cabut kabel USB-nya ~10 detik lalu colok lagi, dan jangan klik eject lagi
  selama drive bersuara.
""",
}


MODE_HINT = {
    "safe": (
        "Aplikasi yang memakai drive TIDAK ditutup - hanya handle file ke drive itu "
        "yang dilepas, jadi VSCode/editor tetap terbuka. Simpan dulu (Ctrl+S): tool "
        "tidak bisa menyimpan dokumen aplikasi lain."
    ),
    "force": (
        "Proses yang memegang drive akan dihentikan paksa (proses sistem dikecualikan). "
        "Data yang belum disimpan di aplikasi itu bisa hilang."
    ),
}
LOG_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "ForceEjectDisk"
)
ICON_NAME = "ForceEjectDisk.ico"


def bus_label(drive: dict) -> str:
    """Keterangan bus drive: USB (bisa dicabut) atau SATA (disk internal)."""
    bus = drive.get("bus")
    if bus == "USB":
        return "USB / bisa dicabut"
    if bus == "internal":
        return "SATA / internal"
    return drive["type"]


def resource_path(name: str) -> str:
    """Lokasi file pendamping: di bundle PyInstaller (sys._MEIPASS) atau di folder script."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, name)


def human_size(num: int) -> str:
    if not num:
        return "-"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < DISK_CAPACITY_STEP:
            if unit in ("B", "KB"):
                return f"{value:.0f} {unit}"
            return f"{value:.1f} {unit}"
        value /= DISK_CAPACITY_STEP
    return f"{value:.1f} PB"


class ForceEjectApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.callback_queue: queue.Queue = queue.Queue()
        self.busy = False
        self.quitting = False
        self.log_file = None
        self.log_path = self._open_log_file()
        self.tray_ok = False
        self._tray_hint_shown = False

        root.title(APP_TITLE)
        self.icon_path = resource_path(ICON_NAME)
        if os.path.exists(self.icon_path):
            try:
                root.iconbitmap(self.icon_path)  # ikon jendela + taskbar
            except tk.TclError as exc:
                self.log(f"! Ikon jendela gagal dimuat: {exc}")
        root.geometry("880x600")
        root.minsize(760, 500)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_widgets()
        self.refresh()
        self._start_tray()
        self.root.after(120, self._drain_log)

        self.log("=" * 64)
        self.log(f"Force Eject Disk - sesi {time.strftime('%Y-%m-%d %H:%M:%S')}")
        self.log(f"Administrator: {'ya' if core.is_admin() else 'TIDAK'}")
        self.log(f"File log: {self.log_path}")
        self.log(
            "Mode awal: aman (hanya lepas handle, aplikasi tetap terbuka). "
            "Pilih mode paksa di panel 'Cara melepas drive' kalau perlu."
        )

    # ------------------------------------------------------------------ log ---
    def _open_log_file(self) -> str:
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            path = os.path.join(LOG_DIR, f"force-eject-{time.strftime('%Y%m%d')}.log")
            self.log_file = open(path, "a", encoding="utf-8")
            return path
        except OSError as exc:
            self.log_file = None
            return f"(gagal membuat file log: {exc})"

    def log(self, message: str) -> None:
        """Aman dipanggil dari thread lain (log ditulis oleh main loop)."""
        self.log_queue.put(str(message))

    def post(self, callback) -> None:
        """Jalankan callback di main loop Tk dari thread lain (tray/pekerja)."""
        self.callback_queue.put(callback)

    def _drain_log(self) -> None:
        while not self.callback_queue.empty():
            try:
                self.callback_queue.get()()
            except Exception as exc:
                self.log(f"ERROR (callback): {exc}")
        while not self.log_queue.empty():
            line = self.log_queue.get()
            stamp = time.strftime("%H:%M:%S")
            self.log_box.configure(state="normal")
            self.log_box.insert("end", line + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
            if self.log_file:
                try:
                    self.log_file.write(f"[{stamp}] {line}\n")
                except OSError:
                    pass
        if self.log_file:
            try:
                self.log_file.flush()
            except OSError:
                pass
        self.root.after(120, self._drain_log)

    def open_log_file(self) -> None:
        if not os.path.exists(self.log_path):
            messagebox.showinfo(APP_TITLE, f"File log belum ada: {self.log_path}")
            return
        try:
            os.startfile(self.log_path)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Tidak bisa membuka log: {exc}")

    # ------------------------------------------------------------------ tray ---
    # -------------------------------------------------------------- bantuan ---
    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(
            label="Cara pakai", accelerator="F1", command=lambda: self.show_help("pakai")
        )
        help_menu.add_command(
            label="Cara kerja & batasan", command=lambda: self.show_help("kerja")
        )
        help_menu.add_separator()
        help_menu.add_command(label="Buka file log", command=self.open_log_file)
        help_menu.add_command(label="Buka folder log", command=self.open_log_folder)
        help_menu.add_separator()
        help_menu.add_command(label="Tentang Force Eject Disk", command=self.show_about)
        menubar.add_cascade(label="Bantuan", menu=help_menu)
        self.root.configure(menu=menubar)
        self.root.bind("<F1>", lambda _event: self.show_help("pakai"))

    def open_log_folder(self) -> None:
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            os.startfile(LOG_DIR)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Tidak bisa membuka folder log: {exc}")

    def show_help(self, topic: str) -> None:
        body = HELP_TEXT.get(topic, "")
        title = "Cara pakai" if topic == "pakai" else "Cara kerja & batasan"
        self._text_dialog(title, body)

    def show_about(self) -> None:
        messagebox.showinfo(
            f"Tentang {APP_TITLE}",
            f"{APP_TITLE} {APP_VERSION}\n\n"
            "Tool Windows untuk mencabut HDD/flashdisk yang masih dipakai aplikasi "
            "latar belakang.\n\n"
            f"Penulis: {APP_AUTHOR}\n"
            f"Lisensi: {APP_LICENSE}\n"
            "Tanpa dependensi eksternal (cukup Python + tkinter).\n\n"
            f"Log: {self.log_path}",
        )

    def _text_dialog(self, title: str, body: str) -> None:
        window = tk.Toplevel(self.root)
        window.title(f"{APP_TITLE} - {title}")
        window.geometry("720x520")
        window.transient(self.root)
        text = tk.Text(window, wrap="word", font=("Consolas", 9), padx=8, pady=8)
        scroll = ttk.Scrollbar(window, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        text.insert("1.0", body.strip())
        text.configure(state="disabled")
        ttk.Button(window, text="Tutup", command=window.destroy).pack(side="bottom", pady=6)
        window.bind("<Escape>", lambda _event: window.destroy())

    def _start_tray(self) -> None:
        self.tray = tray_icon.TrayIcon(
            tooltip=APP_TITLE,
            build_menu=self._tray_menu,
            on_activate=lambda: self.post(self.show_window),
            on_error=self.log,
            icon_path=self.icon_path,
        )
        self.tray_ok = self.tray.start()
        if self.tray_ok:
            self.log("Ikon tray aktif (klik kanan ikon untuk menu eject).")
        else:
            self.log("! Tray tidak tersedia, aplikasi jalan sebagai jendela biasa.")

    def _tray_menu(self):
        items = [("Buka Force Eject Disk", lambda: self.post(self.show_window))]
        try:
            drives = core.list_drives()
        except Exception as exc:
            drives = []
            self.log(f"Gagal membaca daftar drive untuk menu tray: {exc}")
        if drives:
            items.append(None)
            mode = "Eject aman" if self.mode_name == "safe" else "Eject paksa"
            for drive in drives:
                label = f"{mode} {drive['letter']}:  {drive['label'] or drive['type']}"
                items.append((label, self._tray_eject(drive["letter"])))
        items.append(None)
        items.append(("Buka file log", lambda: self.post(self.open_log_file)))
        items.append(("Keluar", lambda: self.post(self.quit_app)))
        return items

    def _tray_eject(self, letter: str):
        return lambda: self.post(lambda: self.eject_drive(letter))

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide_window(self) -> None:
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.notify(APP_TITLE, "Aplikasi masih jalan di tray. Klik kanan ikonnya untuk menu.")
        self.root.withdraw()
        self.log("Jendela disembunyikan ke tray (aplikasi masih berjalan).")

    def on_close(self) -> None:
        if self.tray_var.get() and self.tray_ok:
            self.hide_window()
        else:
            self.quit_app()

    def quit_app(self) -> None:
        if self.quitting:
            return
        self.quitting = True
        self.log("Keluar dari Force Eject Disk.")
        if self.log_file:
            for _ in range(2):  # beri kesempatan baris terakhir tertulis
                self._flush_log_lines()
        self.tray.stop()
        if self.log_file:
            try:
                self.log_file.close()
            except OSError:
                pass
            self.log_file = None
        self.root.destroy()

    def _flush_log_lines(self) -> None:
        while not self.log_queue.empty():
            line = self.log_queue.get()
            if self.log_file:
                try:
                    self.log_file.write(f"[{time.strftime('%H:%M:%S')}] {line}\n")
                except OSError:
                    pass
        if self.log_file:
            try:
                self.log_file.flush()
            except OSError:
                pass

    # ------------------------------------------------------------------ UI ---
    def _build_widgets(self) -> None:
        style = ttk.Style()
        style.configure("Danger.TButton", foreground="#a30000")
        self._build_menu()

        top = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        top.pack(fill="x")

        ttk.Label(top, text="Drive terpasang:", font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="right")
        self.tray_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Tutup jendela -> tray", variable=self.tray_var).pack(
            side="right", padx=10
        )

        modes = ttk.LabelFrame(self.root, text="Cara melepas drive", padding=(10, 6))
        modes.pack(fill="x", padx=10, pady=(0, 6))
        # mode_name disimpan sebagai atribut biasa (bukan tk variable) karena menu
        # tray dibaca dari thread tray, dan tk variable tidak aman di-thread lain.
        self.mode_var = tk.StringVar(value="safe")
        self.mode_name = "safe"
        ttk.Radiobutton(
            modes,
            text="Mode aman: lepas handle saja - aplikasi tetap terbuka (mis. VSCode)",
            value="safe",
            variable=self.mode_var,
            command=self._on_mode_change,
        ).pack(anchor="w")
        ttk.Radiobutton(
            modes,
            text="Mode paksa: tutup proses yang mengunci (data belum disimpan bisa hilang)",
            value="force",
            variable=self.mode_var,
            command=self._on_mode_change,
        ).pack(anchor="w")
        self.mode_hint = ttk.Label(modes, text=MODE_HINT["safe"], foreground="#555555")
        self.mode_hint.pack(anchor="w", pady=(4, 0))
        # sengaja default MATI: perintah eject ke hardware bisa bikin jembatan
        # USB-SATA nyangkut dan drive berputar naik-turun berulang.
        self.media_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            modes,
            text="Kirim perintah eject media ke hardware (hanya kalau cara biasa gagal)",
            variable=self.media_var,
        ).pack(anchor="w", pady=(6, 0))

        columns = ("drive", "label", "type", "size", "lockers")
        self.tree = ttk.Treeview(self.root, columns=columns, show="headings", height=7)
        for col, text, width in (
            ("drive", "Drive", 70),
            ("label", "Nama Volume", 200),
            ("type", "Tipe", 140),
            ("size", "Kapasitas", 110),
            ("lockers", "Pengunci", 110),
        ):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="x", padx=10)
        self.tree.bind("<Double-1>", lambda _event: self.eject_selected())

        actions = ttk.Frame(self.root, padding=(10, 8))
        actions.pack(fill="x")
        self.eject_btn = ttk.Button(
            actions, text="Eject aman", style="Danger.TButton", command=self.eject_selected
        )
        self.eject_btn.pack(side="left")
        self.check_btn = ttk.Button(actions, text="Cek Pengunci", command=self.check_selected)
        self.check_btn.pack(side="left", padx=8)
        ttk.Button(actions, text="Sembunyikan ke tray", command=self.hide_window).pack(side="left")
        ttk.Button(actions, text="Buka file log", command=self.open_log_file).pack(side="left", padx=8)
        ttk.Button(actions, text="Bantuan (F1)", command=lambda: self.show_help("pakai")).pack(
            side="left"
        )
        self.status = ttk.Label(actions, text="Siap.", anchor="e")
        self.status.pack(side="right")

        if not core.is_admin():
            warn = ttk.Frame(self.root, padding=(10, 0))
            warn.pack(fill="x")
            ttk.Label(
                warn,
                text="Tool tidak berjalan sebagai Administrator - beberapa eject akan gagal.",
            ).pack(side="left")
            ttk.Button(warn, text="Jalankan ulang sebagai Admin", command=self.relaunch_as_admin).pack(
                side="left", padx=8
            )

        log_frame = ttk.LabelFrame(self.root, text=f"Log (tersimpan di {self.log_path})", padding=6)
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log_box = tk.Text(log_frame, height=14, wrap="word", state="disabled", font=("Consolas", 9))
        scroll = ttk.Scrollbar(log_frame, command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=scroll.set)
        self.log_box.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    # --------------------------------------------------------------- helpers ---
    def _set_busy(self, busy: bool, status: str = "") -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.eject_btn.configure(state=state)
        self.check_btn.configure(state=state)
        self.status.configure(text=status or ("Bekerja..." if busy else "Siap."))

    def selected_letter(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo(APP_TITLE, "Pilih dulu drive-nya di daftar.")
            return None
        return self.tree.item(selection[0], "values")[0].rstrip(":")

    def _run_async(self, worker, status: str) -> None:
        if self.busy:
            self.log("Masih ada operasi yang berjalan, tunggu sampai selesai.")
            return
        self._set_busy(True, status)

        def runner() -> None:
            try:
                worker()
            except Exception as exc:  # jangan biarkan GUI mati karena error tak terduga
                self.log(f"ERROR: {exc}")
            finally:
                self.post(lambda: self._set_busy(False))

        threading.Thread(target=runner, daemon=True).start()

    # --------------------------------------------------------------- actions ---
    def _on_mode_change(self) -> None:
        self.mode_name = self.mode_var.get()
        self.mode_hint.configure(text=MODE_HINT[self.mode_name])
        self.eject_btn.configure(
            text="Eject aman" if self.mode_name == "safe" else "Eject paksa"
        )

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        try:
            drives = core.list_drives()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Gagal membaca daftar drive: {exc}")
            return
        for drive in drives:
            name = drive["label"] or ("(tanpa nama)" if drive["present"] else "(media kosong)")
            if drive["system"]:
                name += "  [drive sistem]"
            self.tree.insert(
                "",
                "end",
                values=(
                    f"{drive['letter']}:",
                    name,
                    bus_label(drive),
                    human_size(drive["total"]),
                    "-",
                ),
            )
        self.status.configure(text=f"{len(drives)} drive terdeteksi.")

    def check_selected(self) -> None:
        letter = self.selected_letter()
        if not letter:
            return

        def worker() -> None:
            self.log(f"Mencari proses yang memakai {letter}: ...")
            lockers, hidden = core.scan_drive(letter)
            if not lockers:
                self.log("Tidak ada aplikasi yang terdeteksi memegang file di drive ini.")
            for item in lockers:
                tag = " [proses sistem]" if item["protected"] else ""
                self.log(f"  * {item['name']} (PID {item['pid']}){tag}")
                for path in item["paths"]:
                    self.log(f"      {path}")
            if hidden:
                self.log(f"  ! {hidden} proses tidak bisa diperiksa (butuh Administrator).")
            self._update_locker_cell(letter, len(lockers))

        self._run_async(worker, f"Cek pengunci {letter}:")

    def _update_locker_cell(self, letter: str, count: int) -> None:
        def apply() -> None:
            for item in self.tree.get_children():
                if self.tree.item(item, "values")[0].rstrip(":") == letter:
                    values = list(self.tree.item(item, "values"))
                    values[4] = f"{count} proses" if count else "bersih"
                    self.tree.item(item, values=values)
                    break

        self.post(apply)

    def _confirm_eject(self, letter: str, lockers: list[dict], safe: bool) -> bool:
        """Minta konfirmasi sebelum melepas drive. Return False kalau dibatalkan."""
        if safe:
            listed = "\n".join(f"- {item['name']} (PID {item['pid']})" for item in lockers)
            return messagebox.askyesno(
                APP_TITLE,
                f"Mode aman: {len(lockers)} proses ini masih memakai {letter}: dan "
                f"handle-nya akan DILEPAS tanpa aplikasinya ditutup:\n\n{listed}\n\n"
                "Simpan dulu pekerjaanmu (Ctrl+S) - tool ini tidak bisa menyimpan "
                "dokumen aplikasi lain, dan aplikasi tersebut bisa error atau "
                "membuka ulang file-nya setelah drive terlepas.\n\nLanjut?",
            )

        hard = [item for item in lockers if not item["protected"]]
        if not hard:
            return True
        names = "\n".join(f"- {item['name']} (PID {item['pid']})" for item in hard)
        return messagebox.askyesno(
            APP_TITLE,
            f"Proses berikut masih memakai {letter}: dan akan DITUTUP PAKSA "
            f"(data yang belum disimpan bisa hilang):\n\n{names}\n\nLanjut?",
        )

    def eject_selected(self) -> None:
        letter = self.selected_letter()
        if letter:
            self.eject_drive(letter)

    def eject_drive(self, letter: str) -> None:
        letter = core.normalize_letter(letter)
        self.show_window()
        safe = self.mode_name == "safe"
        eject_media = bool(self.media_var.get())

        if core.drive_bus(letter) == "internal":
            self.log(f"{letter}: ada di disk internal (SATA/NVMe) - eject dibatalkan.")
            messagebox.showwarning(
                APP_TITLE,
                f"{letter}: ada di disk internal (SATA/NVMe), bukan drive yang bisa dicabut.\n\n"
                "Windows selalu menolak melepas disk internal, dan lock + dismount-nya bisa "
                "bikin sistem atau disk itu goyang. Pilih drive yang memang eksternal "
                "(kolom Tipe akan menulis 'USB / bisa dicabut').",
            )
            return

        try:
            lockers = core.find_lockers(letter)
        except OSError as exc:
            self.log(f"Gagal memindai handle sistem: {exc}")
            messagebox.showerror(APP_TITLE, f"Gagal memindai handle sistem: {exc}")
            return

        if lockers and not self._confirm_eject(letter, lockers, safe):
            return

        def worker() -> None:
            success = core.force_eject(
                letter,
                kill_lockers=not safe,
                log=self.log,
                safe_mode=safe,
                eject_media=eject_media,
            )
            self.post(self.refresh)
            if not self.root.winfo_viewable():
                self.tray.notify(
                    APP_TITLE,
                    f"{letter}: berhasil di-eject." if success else f"{letter}: gagal di-eject, cek log.",
                )

        self._run_async(worker, f"Eject {letter}: ...")

    def relaunch_as_admin(self) -> None:
        if getattr(sys, "frozen", False):
            exe, params = sys.executable, ""
        else:
            exe, params = sys.executable, f'"{os.path.abspath(__file__)}"'
        try:
            result = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
            if result > 32:
                self.quit_app()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Gagal menjalankan sebagai admin: {exc}")


def main() -> int:
    root = tk.Tk()
    ForceEjectApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
