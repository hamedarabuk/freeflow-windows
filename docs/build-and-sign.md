# FreeFlow: build and release recipe

This is the exact, reproducible process used to produce the v2 release
artefacts. Run all commands from the repo root.

## 1. One-time tooling

```powershell
python -m pip install -r requirements.txt
python -m pip install pyinstaller
winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements
```

Inno Setup installs `ISCC.exe`. On this machine it landed at
`%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`.

## 2. Build the app (PyInstaller, one folder)

```powershell
python -m PyInstaller freeflow.spec --noconfirm --clean
```

Output: `dist\FreeFlow\` (about 127 MB, contains `FreeFlow.exe`).

Notes baked into `freeflow.spec`:
- customtkinter theme assets are bundled (or the app crashes on launch).
- `hooks\hook-webrtcvad.py` overrides the broken contrib hook so the compiled
  `_webrtcvad` extension is bundled and session mode works in the package.
- `uac_admin=True` requests elevation at launch (needed by the default
  `keyboard` backend). Set it False only after confirming the `pynput` backend.

## 3. Portable zip (no installer)

```powershell
python -c "import shutil; shutil.make_archive('dist/FreeFlow-v2','zip',root_dir='dist',base_dir='FreeFlow')"
```

Output: `dist\FreeFlow-v2.zip` (about 58 MB). Members unzip and run
`FreeFlow\FreeFlow.exe`.

## 4. Installer (Inno Setup)

```powershell
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer\freeflow.iss
```

Output: `installer\Output\FreeFlow-Setup.exe` (about 44 MB). Installs to
`%LOCALAPPDATA%\FreeFlow` (no admin to install), adds a Start Menu shortcut,
and offers a login-autostart option.

## 5. Code signing (removes the SmartScreen warning)

Unsigned executables trigger a Windows SmartScreen "Windows protected your PC"
prompt. Members must click "More info" then "Run anyway". Signing removes this
once reputation builds.

What you need:
- An OV (Organisation Validated) code-signing certificate. Since June 2023 all
  publicly trusted code-signing certificates must live on FIPS hardware, so the
  certificate ships on a USB token or via a cloud HSM. It is not a plain `.pfx`
  file any more. Certum's Open Source / OV option (around £60 to £100 per year)
  is the pragmatic pick; EV no longer instantly clears SmartScreen so it is not
  worth the premium.
- `signtool.exe` from the Windows SDK (not currently installed on this machine;
  install "Windows SDK Signing Tools" or the full SDK).

Sign the app exe first, then the installer:

```powershell
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /n "Your Publisher Name" "dist\FreeFlow\FreeFlow.exe"
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer\freeflow.iss
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /n "Your Publisher Name" "installer\Output\FreeFlow-Setup.exe"
```

(With a token, the CSP prompts for the token PIN. Inno Setup can also call
signtool automatically via a SignTool directive if you prefer.)

## 6. Publish

Upload `FreeFlow-Setup.exe` (preferred) or `FreeFlow-v2.zip` to the Skool
community and/or a GitHub Release. If you use the updater, set
`VERSION_CHECK_URL` in `updater.py` to the raw `version.json` URL and bump
`version.json` for each release.

## Known limitation

If the `webrtcvad` contrib hook regresses again on a future PyInstaller
upgrade, session mode falls back to source-only. The local hook in `hooks/`
is the fix; keep `hookspath=['hooks']` in the spec.
