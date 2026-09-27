; Windows installer for OSRS Toolkit (Inno Setup 6). Formerly "OSRS GE Toolkit".
; Built automatically by .github/workflows/release.yml.
; Local build:  iscc /DMyAppVersion=1.1.0 installer\toolkit.iss

#ifndef MyAppVersion
  #define MyAppVersion "1.1.0"
#endif
#define MyAppName "OSRS Toolkit"
; old name: install folder, exe and "Start with Windows" value keep it so updates stay seamless
#define OldName "OSRS GE Toolkit"
#define MyAppExe "OSRS GE Toolkit.exe"
#define McpExe "mcp\osrs-ge-mcp.exe"
#define MyAppURL "https://github.com/Gorsokk/osrs-toolkit"

[Setup]
AppId={{5DA572CB-7B77-4DD7-8BFB-304B6B70C849}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Gorsokk
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
; per-user install: no admin rights needed
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#OldName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UsePreviousGroup=no
OutputDir=..\dist
OutputBaseFilename=OSRS-Toolkit-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; close the running app (tray) before updating it
AppMutex=OSRSGEToolkit
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[CustomMessages]
english.ConnectClaude=Connect to the Claude desktop app (recommended) — ask Claude about your account
french.ConnectClaude=Connecter à l'application Claude (recommandé) — pose des questions à Claude sur ton compte
english.StartWithWindows=Start with Windows (runs quietly in the tray)
french.StartWithWindows=Démarrer avec Windows (discrètement, près de l'horloge)
english.Extras=Extras:
french.Extras=Options :

[Tasks]
Name: "connectclaude"; Description: "{cm:ConnectClaude}"; GroupDescription: "{cm:Extras}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startup"; Description: "{cm:StartWithWindows}"; GroupDescription: "{cm:Extras}"; Flags: unchecked

[Files]
Source: "..\dist\OSRS GE Toolkit\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; shortcuts from before the rename
Type: files; Name: "{autodesktop}\{#OldName}.lnk"
Type: filesandordirs; Name: "{userprograms}\{#OldName}"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Registry]
; same value the app's "Start with Windows" switch uses
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#OldName}"; \
  ValueData: """{app}\{#MyAppExe}"" --background"; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\{#McpExe}"; Parameters: "--connect-claude"; Flags: runhidden waituntilterminated; Tasks: connectclaude; \
  StatusMsg: "Connecting to Claude..."
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#McpExe}"; Parameters: "--disconnect-claude"; Flags: runhidden waituntilterminated; RunOnceId: "DisconnectClaude"

; Settings and history (%APPDATA%\OSRS GE Toolkit) are kept on uninstall, so updates lose nothing.
