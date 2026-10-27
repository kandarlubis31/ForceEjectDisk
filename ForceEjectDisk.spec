# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['force_eject.py'],
    pathex=['.'],
    binaries=[],
    # ikon disertakan juga sebagai data: dipakai ikon tray + ikon jendela saat runtime
    datas=[('ForceEjectDisk.ico', '.')],
    hiddenimports=['ejector_core', 'tray_icon'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ForceEjectDisk',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # ikon aplikasi (juga jadi ikon di Explorer/taskbar)
    icon='ForceEjectDisk.ico',
    # minta UAC saat dijalankan: wajib untuk lock/dismount volume & menutup proses
    uac_admin=True,
)
