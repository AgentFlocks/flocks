; Inno Setup 6 — install Inno Setup, then compile e.g.:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\windows\flocks-setup.iss /DStagingRoot=C:\path\to\staging
; StagingRoot = output directory of packaging\windows\build-staging.ps1

#ifndef StagingRoot
  #define StagingRoot "dist\staging"
#endif

#define MyAppName "Flocks"
#define MyAppVersion "2026.4.16"
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

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "{#StagingRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: string; ValueName: "FLOCKS_INSTALL_ROOT"; ValueData: "{app}"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Environment"; ValueType: string; ValueName: "FLOCKS_REPO_ROOT"; ValueData: "{app}\flocks"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Environment"; ValueType: string; ValueName: "FLOCKS_NODE_HOME"; ValueData: "{app}\tools\node"; Flags: uninsdeletevalue

[Icons]
Name: "{autoprograms}\{#MyAppName}\Start Flocks"; Filename: "{app}\bin\flocks.cmd"; Parameters: "start"; WorkingDir: "{app}"
Name: "{autoprograms}\{#MyAppName}\Flocks repository"; Filename: "{app}\flocks"; WorkingDir: "{app}\flocks"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\bin\flocks.cmd"; Parameters: "start"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\flocks\scripts\bootstrap-windows.ps1"" -InstallRoot ""{app}"""; StatusMsg: "Setting up Python and JavaScript dependencies..."; Flags: runascurrentuser waituntilterminated
