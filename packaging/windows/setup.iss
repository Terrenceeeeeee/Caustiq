; Local evaluation installer template. Do not distribute until exact binary
; dependencies, third-party notices, LGPL obligations, and installer behavior
; have been audited and tested.
#define AppVersion "2.4.2"
[Setup]
AppId={{90CBCA35-C27F-4E83-B626-2BD22B56A422}
AppName=Caustiq
AppVersion={#AppVersion}
AppPublisher=Caustiq contributors
SourceDir=..\..
DefaultDirName={localappdata}\Programs\Caustiq
DefaultGroupName=Caustiq
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
OutputDir=output
OutputBaseFilename=Caustiq_2.4.2_Setup_x64
SetupIconFile=assets\caustiq.ico
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\Caustiq.exe

[Files]
Source: "dist\Caustiq\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "LEGAL_NOTICE.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "docs\CALCULATION_PRINCIPLES.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "assets\caustiq.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "third_party_licenses\*"; DestDir: "{app}\third_party_licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\Caustiq"; Filename: "{app}\Caustiq.exe"; WorkingDir: "{app}"; IconFilename: "{app}\caustiq.ico"; AppUserModelID: "Caustiq.Desktop"
Name: "{group}\Caustiq"; Filename: "{app}\Caustiq.exe"; WorkingDir: "{app}"; IconFilename: "{app}\caustiq.ico"; AppUserModelID: "Caustiq.Desktop"
Name: "{group}\Uninstall Caustiq"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\Caustiq.exe"; Description: "Launch Caustiq"; Flags: nowait postinstall skipifsilent
