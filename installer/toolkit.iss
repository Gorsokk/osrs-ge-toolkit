; Installateur Windows de OSRS GE Toolkit (Inno Setup 6).
; Construit automatiquement par .github/workflows/release.yml.
; Build local:  iscc /DMyAppVersion=1.0.0 installer\toolkit.iss

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppName "OSRS GE Toolkit"
#define MyAppExe "OSRS GE Toolkit.exe"
#define MyAppURL "https://github.com/Gorsokk/osrs-ge-toolkit"

[Setup]
AppId={{5DA572CB-7B77-4DD7-8BFB-304B6B70C849}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Gorsokk
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
; Installation par utilisateur: pas besoin de droits administrateur
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=OSRS-GE-Toolkit-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; relancer l'installateur ferme d'abord une version en cours
CloseApplications=yes

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\OSRS GE Toolkit\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\Dossier de configuration"; Filename: "{userappdata}\{#MyAppName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

; La config et l'historique (%APPDATA%\OSRS GE Toolkit) sont gardes a la desinstallation,
; pour ne rien perdre lors d'une mise a jour.
