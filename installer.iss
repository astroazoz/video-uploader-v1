; Inno Setup script for Video Downloader.
; Build with build_installer.bat (it builds the app first), or open this file in Inno Setup and press Compile.
; Output: installer_output\VideoDownloader-Setup-<version>.exe

#define AppName "Video Downloader"
#define AppVersion "3.0"
#define AppExe "VideoDownloader.exe"

[Setup]
AppId={{2F8BD7EC-8DE9-461D-87B1-447EDA799053}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Installs for the current user by default (no administrator prompt); the wizard offers "for all users".
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=installer_output
OutputBaseFilename=VideoDownloader-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern dynamic
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "dist\VideoDownloader\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
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
