@echo off
REM Build Force Eject Disk jadi satu file EXE (dist\ForceEjectDisk.exe)
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller || goto :error
python -m PyInstaller --noconfirm --clean ForceEjectDisk.spec || goto :error
echo.
echo Selesai. EXE ada di: dist\ForceEjectDisk.exe
goto :eof

:error
echo.
echo Build GAGAL. Cek pesan error di atas.
exit /b 1
