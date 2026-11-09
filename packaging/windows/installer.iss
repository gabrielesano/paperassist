#ifndef AppVersion
  #error AppVersion must be supplied by build_installer.py
#endif

[Setup]
AppId=PaperAssist.Native
AppName=PaperAssist
AppVersion={#AppVersion}
VersionInfoVersion={#AppNumericVersion}
AppPublisher=PaperAssist
AppPublisherURL=https://github.com/gabrielesano/paperassist
DefaultDirName={localappdata}\Programs\PaperAssist
DefaultGroupName=PaperAssist
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
AppMutex=Local\PaperAssist.Native.Reader
SetupMutex=PaperAssist.Native.Setup
CloseApplications=no
RestartApplications=no
WizardStyle=modern
LicenseFile={#BundleDir}\LICENSE
SetupIconFile={#AppIcon}
UninstallDisplayIcon={app}\PaperAssist.exe
OutputDir={#OutputDir}
OutputBaseFilename=PaperAssist-{#AppVersion}-windows-x86_64-setup
Compression=lzma2
SolidCompression=yes
ChangesAssociations=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "pdfhandler"; Description: "Add PaperAssist to Open with for PDF files"; Flags: unchecked

[Registry]
Root: HKCU; Subkey: "Software\Classes\PaperAssist.PDF"; ValueType: string; ValueName: ""; ValueData: "PDF document"; Tasks: pdfhandler; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\PaperAssist.PDF\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\PaperAssist.exe,0"; Tasks: pdfhandler
Root: HKCU; Subkey: "Software\Classes\PaperAssist.PDF\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\PaperAssist.exe"" ""%1"""; Tasks: pdfhandler
Root: HKCU; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "PaperAssist.PDF"; ValueData: ""; Tasks: pdfhandler; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\PaperAssist\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "PaperAssist"; Tasks: pdfhandler; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\PaperAssist\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Read and annotate research PDFs in a local library"; Tasks: pdfhandler
Root: HKCU; Subkey: "Software\PaperAssist\Capabilities\FileAssociations"; ValueType: string; ValueName: ".pdf"; ValueData: "PaperAssist.PDF"; Tasks: pdfhandler
Root: HKCU; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "PaperAssist"; ValueData: "Software\PaperAssist\Capabilities"; Tasks: pdfhandler; Flags: uninsdeletevalue

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Manifest}"; DestDir: "{app}"; DestName: ".paperassist-windows-install"; Flags: ignoreversion
Source: "{#CompilerLicense}"; DestDir: "{app}"; DestName: "INNO-SETUP-LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\PaperAssist"; Filename: "{app}\PaperAssist.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\PaperAssist"; Filename: "{app}\PaperAssist.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\PaperAssist.exe"; Description: "Launch PaperAssist"; Flags: nowait postinstall skipifsilent

[Code]
function IsManagedInstall(const Directory: String): Boolean;
var
  Contents: AnsiString;
begin
  Result := LoadStringFromFile(AddBackslash(Directory) + '.paperassist-windows-install', Contents) and
    (Pos('Managed-By: PaperAssist-Windows-Installer', String(Contents)) > 0) and
    (Pos('AppId=PaperAssist.Native', String(Contents)) > 0);
end;

function HasEntries(const Directory: String): Boolean;
var
  Entry: TFindRec;
begin
  Result := False;
  if FindFirst(AddBackslash(Directory) + '*', Entry) then
  begin
    try
      repeat
        if (Entry.Name <> '.') and (Entry.Name <> '..') then
        begin
          Result := True;
          Break;
        end;
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Directory: String;
begin
  Directory := WizardDirValue;
  Result := '';
  if FileExists(AddBackslash(Directory) + 'library.sqlite3') then
    Result := 'Choose a separate application folder. This folder contains a PaperAssist library.'
  else if HasEntries(Directory) and not IsManagedInstall(Directory) then
    Result := 'The selected folder contains files not managed by PaperAssist. Choose an empty folder.';
end;

function InitializeUninstall(): Boolean;
begin
  Result := not FileExists(ExpandConstant('{app}\library.sqlite3')) and
    IsManagedInstall(ExpandConstant('{app}'));
  if not Result then
    SuppressibleMsgBox('This folder is not a safe PaperAssist application folder. No files were removed.', mbError, MB_OK, IDOK);
end;
