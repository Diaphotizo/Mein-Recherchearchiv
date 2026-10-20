#define AppVersion "1.0.0"
#ifndef PayloadDir
  #define PayloadDir ".."
#endif
#ifndef SetupAppId
  #define SetupAppId "MeinRecherchearchiv.Windows"
#endif
#ifndef SetupAppName
  #define SetupAppName "Mein Recherchearchiv"
#endif
#ifndef OutputName
  #define OutputName "Mein-Recherchearchiv-Setup-1.0.0-Windows11-x64"
#endif
#ifdef EnableSigning
  #ifndef SignedReleaseDir
    #error SignedReleaseDir must name a new output directory for a signed build.
  #endif
#endif

[Setup]
AppId={#SetupAppId}
AppName={#SetupAppName}
AppVersion={#AppVersion}
AppVerName={#SetupAppName} {#AppVersion}
VersionInfoDescription=Mein Recherchearchiv - BIP, ICC/ICJ und UN
DefaultDirName={localappdata}\Programs\Mein Recherchearchiv
DefaultGroupName={#SetupAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
#ifdef IsolatedTest
CreateUninstallRegKey=no
#endif
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
#ifdef IsolatedTest
; GitHub's Windows-2022 runner uses Windows Server 2022 (build 20348).
; Only the disposable installation test accepts this older kernel.
MinVersion=10.0.17763
#else
MinVersion=10.0.22000
#endif
#ifdef SignedReleaseDir
OutputDir={#SignedReleaseDir}
#else
OutputDir=output
#endif
OutputBaseFilename={#OutputName}
#ifdef EnableSigning
SignTool=recherche_release_signer
SignedUninstaller=yes
SignedUninstallerDir={#SignedReleaseDir}\uninstaller
#endif
SetupIconFile={#PayloadDir}\Recherche\web\archive.ico
UninstallDisplayIcon={app}\Recherche\web\archive.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile={#PayloadDir}\LICENSE
InfoBeforeFile=hinweise.txt
CloseApplications=no
RestartApplications=no
RestartIfNeededByRun=no
DisableWelcomePage=no
UninstallDisplayName={#SetupAppName} (BIP, ICC/ICJ, UN)
SetupLogging=yes
UsePreviousAppDir=yes

[Languages]
Name: german; MessagesFile: compiler:Languages\German.isl

[Tasks]
Name: desktopicon; Description: Desktopverknüpfung anlegen; GroupDescription: Verknüpfungen:

[Files]
; Exact allowlisted file entries are generated; user data is never globbed.
#include "build\files.iss"

[Icons]
Name: {group}\Mein Recherchearchiv; Filename: {app}\runtime\pythonw.exe; Parameters: "-B ""{app}\archiv_start.py"""; WorkingDir: {app}; IconFilename: {app}\Recherche\web\archive.ico; AppUserModelID: MeinRecherchearchiv.Windows
Name: {group}\BIP; Filename: {app}\runtime\pythonw.exe; Parameters: "-B ""{app}\archiv_start.py"" --collection bip"; WorkingDir: {app}; IconFilename: {app}\BIP\recherche\web\bip.ico
Name: {group}\ICC-ICJ; Filename: {app}\runtime\pythonw.exe; Parameters: "-B ""{app}\archiv_start.py"" --collection courts"; WorkingDir: {app}; IconFilename: {app}\Recherche\web\courts.ico
Name: {group}\UN; Filename: {app}\runtime\pythonw.exe; Parameters: "-B ""{app}\archiv_start.py"" --collection un"; WorkingDir: {app}; IconFilename: {app}\Recherche\web\un.ico
Name: {group}\Archiv beenden; Filename: {app}\runtime\pythonw.exe; Parameters: "-B ""{app}\archiv_start.py"" --stop"; WorkingDir: {app}; IconFilename: {app}\Recherche\web\archive.ico
Name: {group}\Anleitung; Filename: {app}\INSTALLATION.html
Name: {group}\Archivordner öffnen; Filename: {app}
Name: {userdesktop}\{#SetupAppName} (Setup); Filename: {app}\runtime\pythonw.exe; Parameters: "-B ""{app}\archiv_start.py"""; WorkingDir: {app}; IconFilename: {app}\Recherche\web\archive.ico; AppUserModelID: MeinRecherchearchiv.Windows; Tasks: desktopicon

[Run]
Filename: {app}\runtime\pythonw.exe; Parameters: "-B ""{app}\archiv_start.py"""; WorkingDir: {app}; Description: Mein Recherchearchiv jetzt öffnen; Flags: postinstall nowait skipifsilent

[Code]
function PrepareArchive(const Folder: String): String;
var
  ExitCode: Integer;
  Python, Helper: String;
begin
  Result := '';
  Python := AddBackslash(Folder) + 'runtime\python.exe';
  Helper := AddBackslash(Folder) + 'installer_support.py';
  if FileExists(Python) and FileExists(Helper) then
  begin
    if not Exec(Python, '-B "' + Helper + '" --prepare', Folder,
      SW_HIDE, ewWaitUntilTerminated, ExitCode) then
      Result := 'Das laufende Archiv konnte nicht geprüft werden. Bitte Archiv beenden und erneut versuchen.'
    else if ExitCode <> 0 then
      Result := 'Das Archiv arbeitet noch oder konnte nicht sicher beendet werden. Bitte Aktualisierungen abwarten und über "Archiv beenden" schließen. Es wurden keine Dokumente entfernt.';
  end
  else if FileExists(AddBackslash(Folder) + 'archiv_start.py') then
    Result := 'Dieser Ordner enthält eine andere Archivinstallation. Bitte einen neuen leeren Ordner wählen. Bestehende Archive werden nicht überschrieben.';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := PrepareArchive(ExpandConstant('{app}'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ExitCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    if not Exec(ExpandConstant('{app}\runtime\python.exe'),
      '-B "' + ExpandConstant('{app}\installer_support.py') + '" --initialize',
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) then
      RaiseException('Die Ersteinrichtung konnte nicht gestartet werden.')
    else if ExitCode <> 0 then
      RaiseException('Die Ersteinrichtung konnte nicht abgeschlossen werden. Hinweise stehen in installer-status.txt im Archivordner.');
  end;
end;

function InitializeUninstall(): Boolean;
var
  Problem: String;
begin
  Problem := PrepareArchive(ExpandConstant('{app}'));
  Result := Problem = '';
  if not Result then
    SuppressibleMsgBox(Problem, mbError, MB_OK, IDOK);
end;
