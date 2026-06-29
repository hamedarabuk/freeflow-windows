# FreeFlow: Install Guide

---

## For members (no Python required)

### Step 1: Download the installer

Go to the [Releases page](https://github.com/hamedarabuk/freeflow-windows/releases) and download the latest `FreeFlow-Setup.exe`.

### Step 2: Run the installer

Double-click `FreeFlow-Setup.exe`.

**SmartScreen warning.** Because the installer is not yet code-signed with a commercial certificate, Windows Defender SmartScreen will show a blue "Windows protected your PC" screen. This is expected. To proceed:

1. Click **More info** (small text beneath the warning).
2. Click **Run anyway**.

The installer does not require administrator rights. It installs FreeFlow into your own `AppData\Local\FreeFlow` folder. You can tick optional boxes during setup to add a Desktop shortcut and to start FreeFlow automatically when you log in.

### Step 3: Paste your Groq API key

On first launch, a small setup dialog appears:

- Click the `console.groq.com` link and sign up (free, no credit card needed).
- Create an API key in the Groq Console.
- Paste the key into the FreeFlow setup dialog and click **Save**.

The key is stored privately on your computer at `%APPDATA%\FreeFlow\config.json`. It is never sent anywhere except directly to Groq's servers when you dictate.

### Done

A tray icon and a small floating gadget appear at the bottom-right of your screen. Hold `Alt+1` to dictate into any application.

---

## Updating to a newer version

When a new version is available, FreeFlow shows a tray notice on startup with a download link. Download the new `FreeFlow-Setup.exe` from the Releases page and run it. The installer upgrades in place; your settings and API key are preserved.

---

## Uninstalling

Go to **Settings > Apps**, find FreeFlow, and click **Uninstall**. This removes the program files and the Start Menu shortcut. Your personal config (`%APPDATA%\FreeFlow\config.json`) and log files are left in place; delete them manually if you want a clean removal.

---

## For the maintainer: how to build and release

### 1. Build with PyInstaller

```powershell
# From the repo root (freeflow-windows/)
pip install pyinstaller
pyinstaller freeflow.spec
# Output: dist/FreeFlow/
```

Verify the build by running `dist\FreeFlow\FreeFlow.exe` before packaging.

### 2. Package with Inno Setup

Install [Inno Setup 6](https://jrsoftware.org/isdl.php), then:

```powershell
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\freeflow.iss
# Output: installer\Output\FreeFlow-Setup.exe
```

Or open `installer\freeflow.iss` in the Inno Setup IDE and press **Build > Compile**.

Before compiling, update the `AppVersion` line in `freeflow.iss` to match the new version, and bump `__version__` in `version.py`.

### 3. Upload to GitHub Releases

Create a new release on GitHub tagged with the version (e.g. `v2.1.0`). Upload `FreeFlow-Setup.exe` as a release asset.

### 4. Update version.json

Edit `version.json` at the repo root:

```json
{
  "version": "2.1.0",
  "url": "https://github.com/hamedarabuk/freeflow-windows/releases/download/v2.1.0/FreeFlow-Setup.exe",
  "notes": "FreeFlow v2.1.0"
}
```

Commit and push. The file at the raw GitHub URL is what `updater.py` fetches. Also update the `VERSION_CHECK_URL` constant in `updater.py` to point at the correct raw URL if you have not done so already.

### 5. Code signing (recommended next step)

The SmartScreen "More info" workaround is acceptable for a small community, but it erodes trust. The recommended next step is to purchase an OV (Organisation Validation) code-signing certificate:

- **Certum Open Source** (individual, UK): approximately £60/yr. Removes the "Unknown Publisher" label and starts reputation building with SmartScreen immediately.
- Sign the built `FreeFlow-Setup.exe` with `signtool.exe` (part of the Windows SDK) before uploading to GitHub Releases.
- SmartScreen reputation builds over weeks to months as members download the signed installer.

EV (Extended Validation) certificates no longer bypass SmartScreen instantly (as of March 2024) and cost significantly more. An OV cert is the right choice for this tool at current scale.
