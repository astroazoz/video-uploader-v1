; Inno Setup script for Video Downloader.
; Build with:  python build.py --installer   (builds the app first and passes /DAppVersion=<version>)
; Output: installer_output\VideoDownloader-Setup.exe  (the name is fixed so the download link never changes)

#define AppName "Video Downloader"
#ifndef AppVersion
  #define AppVersion "3.0.0"
#endif
#define AppExe "VideoDownloader.exe"
#define AppURL "https://github.com/astroazoz/video-uploader-v1"

[Setup]
AppId={{2F8BD7EC-8DE9-461D-87B1-447EDA799053}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Video Downloader
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
UsePreviousAppDir=yes
; Installs for the current user: no administrator prompt and no "for all users?" question for the person installing.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=installer_output
OutputBaseFilename=VideoDownloader-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern dynamic
; A running copy is closed through Windows' Restart Manager before its files are replaced (see also InitializeSetup).
CloseApplications=yes
CloseApplicationsFilter=*.exe
RestartApplications=no
ShowLanguageDialog=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; An update replaces the program files completely, so nothing stale from an older version is left behind.
; (User data lives in %APPDATA%\VideoDownloader, a different place, and is never touched here.)
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\VideoDownloader\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open {#AppName}"; Flags: nowait postinstall skipifsilent
; After an in-app update (silent), reopen the app so the update feels seamless.
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser; Check: IsUpdateRun

[Code]
// An in-app update starts setup a moment before the app has finished closing. The app holds a named mutex while it
// runs, so wait (up to 20 s) for it to go away; anything still open afterwards is closed by CloseApplications.
function InitializeSetup: Boolean;
var
  I: Integer;
begin
  for I := 1 to 40 do
  begin
    if not CheckForMutexes('VideoDownloaderAppMutex') then Break;
    Sleep(500);
  end;
  Result := True;
end;

function IsUpdateRun: Boolean;
begin
  Result := ExpandConstant('{param:UPDATED|0}') = '1';
end;

// Uninstall never touches downloaded videos. It only offers to remove the app's own data:
// the library list, settings, thumbnails and any connected-account sessions.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\VideoDownloader');
    if DirExists(DataDir) and (not UninstallSilent) then
    begin
      if MsgBox('Also delete the app''s saved data (library list, settings and connected-account sessions)?' + #13#10 + #13#10 +
                'Your downloaded videos and photos are never deleted.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
