# freeflow.spec — PyInstaller build spec for FreeFlow (one-folder mode).
#
# HOW TO BUILD:
#   1. Install PyInstaller: pip install pyinstaller
#   2. From the repo root (where this file lives), run:
#        pyinstaller freeflow.spec
#   3. The output lands in dist/FreeFlow/
#   4. Run dist/FreeFlow/FreeFlow.exe to verify before packaging with Inno Setup.
#
# ADMIN NOTE:
#   uac_admin=True embeds a Windows manifest requesting "requireAdministrator".
#   This is required by the default "keyboard" input backend, which uses a
#   low-level WH_KEYBOARD_LL hook that needs elevation.
#   If you switch to input_backend = "pynput" in settings.json and confirm it
#   works for all your target apps, you can set uac_admin=False here, rebuild,
#   and the UAC prompt disappears entirely.
#
# ICON:
#   The tray icon is generated at runtime (no external .ico file).
#   If you later add a branded .ico file, point the `icon` parameter below at it:
#     icon='assets/freeflow.ico'

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

_datas = [
    # Prompt templates (cleanup instructions per mode)
    ('prompts', 'prompts'),
    # User-config example files — seeded into the install folder so
    # users can inspect the schema without reading source code.
    ('dictionary.json.example', '.'),
    ('snippets.json.example', '.'),
    ('settings.json.example', '.'),
    # Version manifest — read by updater.py at dev/test time.
    ('version.json', '.'),
]
# customtkinter ships JSON themes and image assets that must be bundled, or the
# built app crashes on launch with a missing-theme error.
_datas += collect_data_files('customtkinter')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=_datas,
    hiddenimports=[
        # pystray enumerates backends at runtime; include all Windows variants.
        'pystray._win32',
        # pywin32 service modules sometimes need explicit inclusion.
        'win32api',
        'win32con',
        'win32gui',
    ],
    # Local hooks/ overrides the broken pyinstaller-hooks-contrib webrtcvad
    # hook (see hooks/hook-webrtcvad.py). This re-enables session mode in the
    # packaged build.
    hookspath=['hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FreeFlow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # Windowed app; no console window shown on launch.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,         # Requests elevation for the keyboard backend.
                            # Set False for a pynput no-admin build.
    # icon='assets/freeflow.ico',   # Uncomment once you have a branded icon.
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FreeFlow',        # Output folder: dist/FreeFlow/
)
