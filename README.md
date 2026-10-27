# Force Eject Disk

Windows tool to **unplug an HDD/flash drive that refuses to eject** because a
background app (Windows Explorer, editor, media player, backup or indexing) still
holds it. Available as a single EXE on the
[Releases page](../../releases) — no Python needed.

[![Build EXE](https://github.com/kandarlubis31/ForceEjectDisk/actions/workflows/build.yml/badge.svg)](https://github.com/kandarlubis31/ForceEjectDisk/actions/workflows/build.yml)

### English

Unlike Windows' own "Safely Remove Hardware", this tool tells you **which app**
is holding the drive, gives you its file paths, and then lets you pick how to
release it:

- **Safe mode (default)** — closes *only the file handles* that point at that
drive (the same trick as Sysinternals `handle.exe -c`), after flushing file
buffers. **The app stays open**: your editor keeps running, it just loses access
to that drive. Nothing is killed.
- **Force mode** — terminates the processes that hold the drive (core Windows
  processes are never touched). Unsaved data in those apps can be lost.

Then it locks → dismounts → asks the Plug and Play manager to remove the device
(`CM_Request_Device_EjectW`), exactly like "Safely Remove Hardware" does.

The drive list shows the bus, so you cannot shoot yourself in the foot:
`USB / bisa dicabut` (removable) vs `SATA / internal`. Internal disks are
**refused**, because Windows never lets you eject them and locking/dismounting
them can destabilise the system.

The hardware media-eject command (`IOCTL_STORAGE_EJECT_MEDIA`) is **off by
default**: on USB-SATA bridges it can wedge the enclosure and make the drive
spin up/down in a loop. Tick the checkbox only if the normal route fails.

Other features: system tray icon with per-drive quick eject menu, every log line
written to `%LOCALAPPDATA%\ForceEjectDisk\force-eject-YYYYMMDD.log`, help menu
(F1), no external dependencies (stdlib `ctypes` + `tkinter` only), single-file
EXE that requests Administrator rights via UAC on launch.

```
Run   : dist\ForceEjectDisk.exe   (asks for Administrator)
Source: python force_eject.py     (Windows, Python 3.10+)
Build : build.bat  or  python -m PyInstaller --noconfirm --clean ForceEjectDisk.spec
```

License: MIT. Notes: Windows only; safe mode cannot release memory-mapped files
(e.g. a running `flutter run`/Dart VM) nor save someone else's unsaved document.

---

## Bahasa Indonesia

Tool Windows untuk **mencabut HDD/flashdisk yang menolak di-eject** karena masih
dipakai aplikasi latar belakang (Windows Explorer, media player, backup, indexing).

Bedanya dengan "Safely Remove Hardware" bawaan Windows: tool ini mencari tahu
**aplikasi mana** yang masih memegang drive tersebut, lalu memaksa volume
di-lock + dismount + eject sampai device-nya dilepas. Ada dua cara melepasnya:
mode **aman** (aplikasi tetap terbuka) dan mode **paksa** (aplikasi ditutup).

## Cara pakai

1. Jalankan `ForceEjectDisk.exe` (minta izin Administrator — wajib).
2. Klik **Refresh**, pilih drive di daftar.
3. Klik **Cek Pengunci** kalau mau tahu dulu aplikasi mana yang menahan.
4. Pilih cara melepas di panel **Cara melepas drive**, lalu klik **Eject aman** /
   **Eject paksa**. Kalau ada aplikasi pengunci, muncul konfirmasi berisi daftar
   aplikasi itu.
5. Kalau log bilang "berhasil di-eject", drive sudah aman dicabut.

Kolom **Tipe** di daftar drive menuliskan bus-nya: **USB / bisa dicabut** atau
**SATA / internal**. Disk internal **ditolak** oleh tool ini (lihat di bawah).

### Disk internal ditolak (dan kenapa)

Disk `C:`, atau disk SATA/NVMe apa pun, tidak bisa dicabut — Windows selalu
menolak permintaan pelepasannya, dan `FSCTL_LOCK_VOLUME` + `FSCTL_DISMOUNT_VOLUME`
pada disk internal justru bisa membuat sistem atau disk itu goyah. Jadi tool ini
menolak dengan pesan jelas, bukan mencoba dan meninggalkan drive dalam keadaan
tanggung (dismount tapi device-nya masih nempel).

### Mode aman: lepas handle, aplikasi tetap terbuka

Ini untuk kasus seperti: **VSCode (dari `C:`/`D:`) sedang membuka project yang
ada di drive yang mau dicabut**. Mode paksa akan menutup VSCode — mode aman
**tidak menyentuh proses sama sekali**, cuma melepas handle file yang menunjuk
ke drive itu (cara kerjanya sama dengan `handle.exe -c` dari Sysinternals).

Alurnya:

1. Isi buffer file di-flush dulu (`FlushFileBuffers`) supaya tulisan yang belum
   sampai ke disk tidak hilang.
2. Handle-handle yang menunjuk ke drive itu ditutup di proses pemiliknya.
3. Aplikasi tetap jalan seperti biasa — yang hilang hanya aksesnya ke drive
   tersebut. Kalau aplikasi itu masih butuh drive-nya, dia akan error sendiri
   atau membuka ulang file (drive sudah lepas, jadi gagal).
4. Log merinci per aplikasi: berapa handle yang dilepas, berapa file di-flush,
   dan file apa saja yang tadi dipegang.

**Simpan dulu (Ctrl+S)** sebelum klik. Tool ini tidak bisa menyimpan dokumen
milik aplikasi lain; flush hanya menjamin data yang sudah "diserahkan" aplikasi
ke Windows benar-benar ditulis ke disk. Handle milik proses inti Windows
(`system`, `smss`, `csrss`, `wininit`, `winlogon`, `services`, `lsass`) tidak
dilepas sama sekali — kalau salah satu dari itu yang menahan, pakai mode paksa
atau cabut lewat Safely Remove Hardware.

### Perintah eject ke hardware (default: mati)

Centang **Kirim perintah eject media ke hardware** hanya kalau cara biasa gagal.
Yang dikirim tambahan adalah `IOCTL_STORAGE_EJECT_MEDIA` ke perangkatnya, artinya
minta hardware memutar-turun/mengeluarkan medianya.

Default-nya **mati** karena perintah itu bisa membuat **jembatan USB-SATA
nyangkut**: Windows menganggap medianya dikeluarkan, tapi device-nya masih
terpasang, jadi Windows bolak-balik memeriksa drive-nya dan drive itu terdengar
**berputar naik-turun berulang**. Eject standar (lock + dismount + lepas device
lewat PnP manager) sudah cukup dan itulah yang dilakukan "Safely Remove
Hardware" bawaan Windows.

### Mode tray

Ikon aplikasi selalu aktif di system tray selama aplikasi berjalan.

- **Klik kanan ikon tray** → menu: `Buka Force Eject Disk`, `Eject aman C:`,
  `Eject aman D:`, … (satu entri per drive yang terpasang, mengikuti mode yang
  sedang dipilih di jendela), `Buka file log`, `Keluar`.
- **Klik kiri ikon tray** → munculkan jendela lagi.
- Klik entri eject di menu tray = sama dengan menekan tombol Eject pada drive itu:
  jendela muncul, konfirmasi tetap ditampilkan, lalu prosesnya jalan dan hasilnya
  muncul sebagai notifikasi balon kalau jendela sedang disembunyikan.
- Tombol X di jendela **tidak menutup aplikasi** selama centang
  "Tutup jendela -> tray" aktif; aplikasi tetap jalan di tray. Hilangkan
  centangnya kalau mau tombol X langsung keluar, atau pakai `Keluar` di menu tray.

### Ikon

Ikon aplikasi dibuat sendiri (`ForceEjectDisk.ico`, glyf eject putih di tile biru)
dan dipakai di tiga tempat: ikon file EXE (Explorer/taskbar), ikon system tray, dan
ikon jendela. Tidak ada dependensi tambahan — ikonnya digambar dan dikemas jadi
PNG/ICO oleh `make_icon.py`:

```
python make_icon.py
```

Jalankan itu kalau mau ganti warna/bentuk ikon; variabel warna dan geometrinya ada
di bagian atas file. Ukuran yang dibuat: 16, 24, 32, 48, 64, 128, 256 piksel.

### Kalau drive USB jadi bunyi berputar berulang

Kalau setelah eject drive USB-nya bersuara berulang (spin-up/spin-down):

1. **Cabut kabel USB-nya** (atau matikan enclosure-nya), tunggu ~10 detik, lalu
   colok lagi. Itu me-reset jembatan USB-SATA-nya dan biasanya suara berulangnya
   langsung berhenti.
2. Jangan klik **Eject** berulang-ulang sambil drive-nya bersuara — itu
   menambah siklus spin down/up.
3. Kalau suaranya **mekanis/dek-tik keras dan datar** (bukan berputar), itu gejala
   drive mau rusak: hentikan pemakaian, salin dulu data pentingnya, dan jangan
   di-eject berulang.

Data tetap aman pada kasus ini: tool tidak pernah menulis ke drive, dan sebelum
dismount isi buffer file sudah di-flush.

### File log

Semua baris log (termasuk waktu, status Administrator, dan langkah eject)
langsung ditulis ke file dengan timestamp:

```
%LOCALAPPDATA%\ForceEjectDisk\force-eject-YYYYMMDD.log
```

Jadi bisa dicek setelah aplikasi ditutup, atau saat eject gagal. Klik
**Buka file log** di jendela (atau di menu tray) untuk membukanya.

Catatan:
- HDD eksternal lewat USB sering terdeteksi sebagai tipe **"Fixed / eksternal"**,
  bukan "Removable" — itu normal, tetap bisa di-eject.
- Lebih membingungkan lagi: enclosure USB modern (UASP) tampil sebagai
  **"SCSI Disk Device"** dan tidak muncul sebagai `USBSTOR` sama sekali. Karena
  itu tool ini menentukan bus lewat **devnode induk disk-nya** (kalau induknya
  device `USB\...` berarti eksternal), bukan dari nama atau kelas device-nya.
- Proses sistem (System, csrss, lsass, svchost, explorer, dll.) tidak pernah
  ditutup paksa oleh tool ini.
- Kalau log berakhir "MASIH terpasang", jalankan sebagai Administrator, atau
  tutup manual aplikasi yang disebut di log.

## Bantuan di dalam aplikasi

Menu **Bantuan** (atau tombol **Bantuan (F1)**) berisi: **Cara pakai**, **Cara
kerja & batasan**, **Buka file log**, **Buka folder log**, dan **Tentang** (versi,
penulis, lisensi). Isinya sama dengan dokumentasi di README ini, tapi bisa dibaca
tanpa keluar dari aplikasi.

## Jalan tanpa build

```
python force_eject.py
```

Butuh Python 3.10+ (tanpa dependensi eksternal, cuma stdlib + tkinter).
Untuk hak admin, klik kanan → Run as administrator, atau pakai tombol
"Jalankan ulang sebagai Admin" di dalam aplikasi.

## Build EXE (PyInstaller)

```
build.bat
```

atau manual:

```
python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean ForceEjectDisk.spec
```

Hasil: `dist\ForceEjectDisk.exe` (satu file, sudah meminta UAC admin otomatis).

Isi project: `force_eject.py` (GUI + tray + log file), `ejector_core.py` (logika
Win32), `tray_icon.py` (ikon system tray pakai ctypes, tanpa dependensi eksternal),
`ForceEjectDisk.ico` (ikon aplikasi), `make_icon.py` (pembuat ikon itu).

## Bagaimana cara kerjanya

1. **Cari pengunci** — seluruh handle sistem dibaca via
   `NtQuerySystemInformation(SystemExtendedHandleInformation)`, tiap handle
   diduplikasi ke proses kita, difilter dengan `GetFileType` (handle pipe/device
   bisa menggantungkan pemanggilan berikutnya), lalu path-nya dibaca dengan
   `GetFinalPathNameByHandleW`. Semua proses yang punya file di drive target
   dilaporkan. (Restart Manager tidak dipakai: pendaftaran direktori tidak
   melaporkan file yang sedang terbuka.)
   Handle yang tidak punya path file - misalnya aplikasi yang memegang langsung
   `\\.\X:` atau `\\.\PhysicalDriveN`, penyebab umum `FSCTL_LOCK_VOLUME` gagal -
   dikenali lewat `IOCTL_STORAGE_GET_DEVICE_NUMBER` dan ditandai
   "(handle langsung ke volume ...)".
2. **Lepaskan drive** — salah satu dari:
   - *mode aman*: tiap handle yang menunjuk ke drive itu ditutup di proses
     pemiliknya lewat `DuplicateHandle(..., DUPLICATE_CLOSE_SOURCE)`, setelah
     `FlushFileBuffers`. Prosesnya tidak dibunuh; setelah itu drive dipindai
     ulang untuk melihat siapa yang masih menahan.
   - *mode paksa*: aplikasi pengunci dihentikan dengan `TerminateProcess`
     (proses sistem dikecualikan).
3. **Eject volume** — buka `\\.\X:`, `IOCTL_STORAGE_MEDIA_REMOVAL` (izinkan
   pelepasan), `FSCTL_LOCK_VOLUME`, `FSCTL_DISMOUNT_VOLUME`.
   `IOCTL_STORAGE_EJECT_MEDIA` hanya dikirim kalau diminta lewat centang
   "Kirim perintah eject media ke hardware".
4. **Lepas device** — kalau drive masih terpasang: lock + dismount di
   `\\.\PhysicalDriveN`, lalu minta PnP manager melepas devnode disk-nya dengan
   `CM_Request_Device_EjectW` (dicari lewat SetupAPI, GUID_DEVINTERFACE_DISK).
   Jadi urutannya sama dengan "Safely Remove Hardware" bawaan Windows.

## Batasan

- Windows saja.
- Operasi lock/dismount/eject butuh Administrator.
- Ikon tray dibuat sendiri dengan `Shell_NotifyIconW` (parent process tidak perlu
  Python tambahan seperti `pystray`/Pillow).
- Antivirus atau Windows Search yang sedang scan bisa menahan drive; tool ini
  tidak menutup proses antivirus (dianggap proses sistem).
- Mode aman melepas handle, tapi tidak bisa **melepas file yang di-map ke memori**
  (memory-mapped file). Ini sering dijumpai pada aplikasi yang sedang jalan dari
  drive itu: `flutter run`/Dart VM, database yang terbuka, atau antivirus yang
  sedang memeriksa file. Untuk kasus itu aplikasinya harus dihentikan dulu
  (mode paksa) — melepas handle saja tidak akan cukup.
- Mode aman juga tidak bisa menyimpan dokumen aplikasi yang belum di-save; itu
  tetap harus kamu lakukan sendiri sebelum eject.
