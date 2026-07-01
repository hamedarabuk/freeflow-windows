; freeflow.iss — Inno Setup script for FreeFlow.
;
; HOW TO COMPILE:
;   1. Install Inno Setup 6 from https://jrsoftware.org/isdl.php
;   2. Open this file in the Inno Setup Compiler (ISCC.exe).
;   3. Press Build > Compile (or Ctrl+F9).
;   4. The output is: installer\Output\FreeFlow-Setup.exe
;
;   Or from the command line:
;     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\freeflow.iss
;
; VERSION:
;   Update AppVersion below and rebuild. Then upload the new Setup.exe to
;   GitHub Releases and update version.json with the matching version string
;   and release URL.
;
; INSTALL LOCATION NOTE:
;   This script installs into {localappdata}\FreeFlow (the user's own AppData).
;   That means NO UAC prompt is required during INSTALLATION, which is much
;   friendlier for members who may not have admin rights on their PC.
;   The app still elevates at RUNTIME (the FreeFlow.exe manifest requests admin
;   for the keyboard hook), so a UAC prompt appears only when you launch it.
;   If you later switch to the pynput no-admin backend (and rebuild the exe
;   without uac_admin=True), runtime elevation disappears entirely and install
;   stays silent.
;
; PREREQUISITES:
;   Run PyInstaller first:
;     pyinstaller freeflow.spec
;   The compiled app must be at:
;     dist\FreeFlow\FreeFlow.exe   (and the rest of the dist\FreeFlow\ folder)

#define AppName      "FreeFlow"
#define AppVersion   "2.0.0"
#define AppPublisher "Hamed Arab Choobdar"
#define AppURL       "https://github.com/hamedarabuk/freeflow-windows"
#define AppExeName   "FreeFlow.exe"

[Setup]
AppId={{B4E1F9C2-3A7D-4F2E-9C8B-1D5E7A3F0B6C}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
; Install under the user's own AppData — no system-wide UAC needed to install.
DefaultDirName={localappdata}\{#AppName}
DefaultGroupName={#AppName}
; Allow users to pick a different folder if they wish.
DisableDirPage=no
OutputDir=Output
OutputBaseFilename=FreeFlow-Setup
; No admin privilege needed for the installer itself (user-only install).
PrivilegesRequired=lowest
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Uncomment and point at your .ico once you have one:
; SetupIconFile=..\assets\freeflow.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Desktop icon — opt-in.
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
  GroupDescription: "{cm:AdditionalIcons}"
; Start at login via HKCU Run — opt-in.
Name: "startupentry"; Description: "Start FreeFlow automatically when I log in"; \
  GroupDescription: "Startup"

[Files]
; Copy the entire PyInstaller one-folder output into the install dir.
Source: "..\dist\FreeFlow\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu shortcut.
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
; Desktop shortcut (only if user ticked the task above).
; {autodesktop} resolves to the per-user desktop under a non-admin install,
; so it never hits the Access-denied error on C:\Users\Public\Desktop.
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
  Tasks: desktopicon

[Registry]
; HKCU Run entry for login autostart (only if user ticked the task above).
; Uses HKCU so no admin rights are needed.
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "{#AppName}"; \
  ValueData: """{app}\{#AppExeName}"""; \
  Flags: uninsdeletevalue; Tasks: startupentry

[Run]
; Offer to launch FreeFlow immediately after installation completes.
Filename: "{app}\{#AppExeName}"; \
  Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; \
  Flags: nowait postinstall skipifsilent

[UninstallRun]
; Clean up the HKCU Run entry on uninstall regardless of whether the task was
; selected (belt-and-braces: the Flags: uninsdeletevalue above handles the
; normal path, this catches edge cases).
Filename: "reg.exe"; \
  Parameters: "delete ""HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"" /v ""{#AppName}"" /f"; \
  RunOnceId: "RemoveStartup"; Flags: runhidden
