# Local override for the broken pyinstaller-hooks-contrib webrtcvad hook.
#
# The contrib hook runs `copy_metadata('webrtcvad')` at import time, which
# raises PackageNotFoundError because the installed distribution is named
# `webrtcvad-wheels` (the prebuilt-wheel fork), not `webrtcvad`. That crash
# aborts the entire PyInstaller build. Hooks in this local hooks/ directory
# (wired via hookspath in freeflow.spec) take precedence over the bundled
# contrib hook, so this file replaces it.
#
# webrtcvad-wheels ships a pure-Python `webrtcvad.py` wrapper plus the compiled
# extension `_webrtcvad`. We pull in the extension explicitly and copy the
# metadata under whichever distribution name is actually present.

from PyInstaller.utils.hooks import collect_dynamic_libs, copy_metadata

# Ensure the compiled extension module is bundled.
hiddenimports = ["_webrtcvad"]

# Grab any shared libraries that ship alongside the extension.
binaries = collect_dynamic_libs("webrtcvad") + collect_dynamic_libs("_webrtcvad")

# Copy metadata under the real distribution name, tolerating either spelling.
datas = []
for _dist in ("webrtcvad-wheels", "webrtcvad"):
    try:
        datas += copy_metadata(_dist)
        break
    except Exception:
        continue
