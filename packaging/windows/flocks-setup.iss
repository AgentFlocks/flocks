; Inno Setup 6 — install Inno Setup, then compile e.g.:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\windows\flocks-setup.iss /DStagingRoot=C:\path\to\staging
; StagingRoot = output directory of packaging\windows\build-staging.ps1

#ifndef StagingRoot
  #define StagingRoot "dist\staging"
#endif

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

#define MyAppName "Flocks"
#define MyAppVersion AppVersion
#define MyAppPublisher "Flocks"

[Setup]
AppId={{A8C9E2F1-4B3D-5E6F-9A0B-1C2D3E4F5A6B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DisableProgramGroupPage=yes
OutputBaseFilename=FlocksSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ChangesEnvironment=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

; Remind the user to reopen their terminal so a fresh process picks up the
; HKCU\Environment entries (FLOCKS_INSTALL_ROOT / FLOCKS_NODE_HOME / PATH)
; written during install; cmd.exe doesn't respond to WM_SETTINGCHANGE, so
; any pre-existing shells keep stale env vars.
[Messages]
FinishedLabel=Setup has finished installing [name] on your computer.%n%nPlease open a NEW terminal window before running `flocks start`, so the updated environment variables (PATH, FLOCKS_NODE_HOME, ...) take effect.%n%n请重新打开终端窗口后再执行 `flocks start`，以便新的环境变量（PATH、FLOCKS_NODE_HOME 等）生效。

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "{#StagingRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: string; ValueName: "FLOCKS_INSTALL_ROOT"; ValueData: "{app}"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Environment"; ValueType: string; ValueName: "FLOCKS_REPO_ROOT"; ValueData: "{app}\flocks"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Environment"; ValueType: string; ValueName: "FLOCKS_NODE_HOME"; ValueData: "{app}\tools\node"; Flags: uninsdeletevalue

; Shortcuts intentionally target the same wrapper path that `scripts\install.ps1`
; writes, so the Start menu / desktop icon and `flocks start` typed in a new
; terminal are strictly equivalent across all install flows.
[Icons]
Name: "{autoprograms}\{#MyAppName}\Start Flocks"; Filename: "{%USERPROFILE}\.local\bin\flocks.cmd"; Parameters: "start"; WorkingDir: "{%USERPROFILE}"
Name: "{autoprograms}\{#MyAppName}\Flocks repository"; Filename: "{app}\flocks"; WorkingDir: "{app}\flocks"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{%USERPROFILE}\.local\bin\flocks.cmd"; Parameters: "start"; WorkingDir: "{%USERPROFILE}"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\flocks\packaging\windows\bootstrap-windows.ps1"" -InstallRoot ""{app}"""; StatusMsg: "Setting up Python and JavaScript dependencies..."; Flags: runascurrentuser waituntilterminated
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""$env:FLOCKS_INSTALL_ROOT = """"{app}""""; $env:FLOCKS_REPO_ROOT = Join-Path $env:FLOCKS_INSTALL_ROOT """"flocks""""; $env:FLOCKS_NODE_HOME = Join-Path $env:FLOCKS_INSTALL_ROOT """"tools\node""""; $env:Path = $env:FLOCKS_NODE_HOME + """";"""" + $env:Path; $pythonExe = Join-Path $env:FLOCKS_REPO_ROOT "".venv\Scripts\python.exe""; if (Test-Path -LiteralPath $pythonExe) {{ Start-Process -FilePath $pythonExe -ArgumentList ""-m"", ""flocks.cli.main"", ""start"" -WorkingDirectory $env:USERPROFILE -WindowStyle Hidden }} else {{ $flocksCmd = Join-Path $env:USERPROFILE "".local\bin\flocks.cmd""; if (Test-Path -LiteralPath $flocksCmd) {{ Start-Process -FilePath $flocksCmd -ArgumentList ""start"" -WorkingDirectory $env:USERPROFILE -WindowStyle Hidden }} }}"""; WorkingDir: "{%USERPROFILE}"; Description: "Launch Flocks now"; Flags: postinstall nowait skipifsilent runascurrentuser

; Runs before [Files] are deleted: flocks stop (graceful), then taskkill fallback, PATH/env/flocks.cmd cleanup, bundled Chrome junction.
[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\flocks\packaging\windows\uninstall-flocks-user-state.ps1"" -InstallRoot ""{app}"""; RunOnceId: "FlocksUninstallCleanup"; Flags: runascurrentuser

; Explicit shortcut removal (desktop / Start menu). Targets outside {app} may not always be tracked by the default icon uninstall.
[UninstallDelete]
Type: files; Name: "{userdesktop}\{#MyAppName}.lnk"
Type: files; Name: "{autoprograms}\{#MyAppName}\Start Flocks.lnk"
Type: files; Name: "{autoprograms}\{#MyAppName}\Flocks repository.lnk"
Type: dirifempty; Name: "{autoprograms}\{#MyAppName}"
; Do not recursively delete {app}\* during uninstall. Users may choose a custom
; existing directory and broad sweep can remove unrelated files.
Type: dirifempty; Name: "{app}"
