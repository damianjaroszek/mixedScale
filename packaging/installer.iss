; Inno Setup script: builds Blendora-Setup-<version>.exe from dist\Blendora
#ifndef Edition
  #define Edition ""
#endif
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6B3E1F7A-4C2D-4E8B-9A51-B1E4D0C7A2F3}
AppName=Blendora
AppVersion={#AppVersion}
AppPublisher=Damian Jaroszek
DefaultDirName={autopf}\Blendora
DefaultGroupName=Blendora
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=Blendora{#Edition}-Setup-{#AppVersion}
SetupIconFile=blendora.ico
UninstallDisplayIcon={app}\Blendora.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\Blendora\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Blendora"; Filename: "{app}\Blendora.exe"
Name: "{group}\Blendora (w przeglądarce)"; Filename: "{app}\Blendora.exe"; Parameters: "--browser"
Name: "{autodesktop}\Blendora"; Filename: "{app}\Blendora.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Blendora.exe"; Description: "{cm:LaunchProgram,Blendora}"; Flags: nowait postinstall skipifsilent
