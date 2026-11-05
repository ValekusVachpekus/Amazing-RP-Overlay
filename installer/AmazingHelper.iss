#ifndef AppVersion
  #error AppVersion must come from scripts/build_distribution.py
#endif
#ifndef BundleDir
  #error BundleDir is required
#endif
#ifndef OutputDir
  #define OutputDir "..\.cache\dist"
#endif
[Setup]
SetupIconFile=..\amazing_overlay\assets\amazing-helper.ico
AppId={{5F1B31C4-08A8-4AE1-9DCD-E62A20280001}
AppName=AmazingHelper
AppVersion={#AppVersion}
AppPublisher=Vladislav_Shetkov
DefaultDirName={autopf}\AmazingHelper
DefaultGroupName=AmazingHelper
UsePreviousAppDir=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=AmazingHelperSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
SetupMutex=Global\AmazingHelper.Setup.v1
UninstallDisplayIcon={app}\AmazingHelper.exe
VersionInfoVersion={#AppVersion}
ChangesAssociations=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; Flags: unchecked

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "Notes\*,local\*,.env,config.json,installed.json"
Source: "installed.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\AmazingHelper"; Filename: "{app}\AmazingHelper.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\AmazingHelper"; Filename: "{app}\AmazingHelper.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\AmazingHelper.exe"; Description: "Открыть AmazingHelper"; Flags: postinstall nowait skipifsilent runasoriginaluser

[Code]
function RuntimeBusy(): Boolean;
begin
  Result := CheckForMutexes('Global\AmazingHelper.Runtime.v1');
end;

function GameBusy(): Boolean;
var Locator, Services, Processes: Variant;
begin
  Result := True;
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Services := Locator.ConnectServer('', 'root\CIMV2');
    Processes := Services.ExecQuery('SELECT ProcessId FROM Win32_Process WHERE Name="amazing.exe" OR Name="gta_sa.exe"');
    Result := Processes.Count > 0;
  except
    { Fail closed when process enumeration is unavailable. }
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if GameBusy() then
    Result := 'Закройте Amazing / GTA SA перед установкой. Игра не будет закрыта автоматически.'
  else if RuntimeBusy() then
    Result := 'Закройте все окна AmazingHelper и штатно остановите бэкенд у всех пользователей, затем нажмите «Повторить».';
end;

function InitializeUninstall(): Boolean;
begin
  Result := not RuntimeBusy() and not GameBusy();
  if not Result then
    MsgBox('Закройте игру, клиенты и бэкенд AmazingHelper перед удалением. Личные данные будут сохранены.', mbError, MB_OK);
end;
