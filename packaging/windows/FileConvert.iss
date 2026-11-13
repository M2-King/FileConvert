; FileConvert installer for Windows (built by GitHub Actions, or locally with Inno Setup 6:
;   iscc /DAppVersion=1.0.0 packaging\windows\FileConvert.iss
; after building dist\FileConvert with PyInstaller).
; Installs for the current user only, so it never asks for admin rights.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{8C4C6E2A-4D7B-4E7A-9C1E-0FC0FC0FC0FC}
AppName=FileConvert
AppVersion={#AppVersion}
AppVerName=FileConvert {#AppVersion}
AppPublisher=FileConvert
DefaultDirName={localappdata}\Programs\FileConvert
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=FileConvert-Setup
SetupIconFile=..\..\assets\icon.ico
UninstallDisplayIcon={app}\FileConvert.exe
UninstallDisplayName=FileConvert
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Messages]
FinishedLabel=FileConvert is ready.%n%nPress and hold a file in File Explorer or on the desktop for half a second, then slide onto a format and let go.%n%nYou can also right-click a file and choose Convert to (on Windows 11: Show more options).

[Files]
Source: "..\..\dist\FileConvert\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Run]
Filename: "{app}\FileConvert.exe"; Parameters: "install --quiet"; StatusMsg: "Adding FileConvert to your mouse menus..."; Flags: waituntilterminated

[UninstallRun]
Filename: "{app}\FileConvert.exe"; Parameters: "uninstall --quiet"; Flags: waituntilterminated; RunOnceId: "FileConvertUninstall"

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\FileConvert"

[Code]
// Stop the running press-and-hold helper before files are replaced (updates).
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
  Exe: String;
begin
  Exe := ExpandConstant('{app}\FileConvert.exe');
  if FileExists(Exe) then
    Exec(Exe, 'stop-agent', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;
