; TradingApp — instalator Windows (Inno Setup 6). Budowany automatycznie przez GitHub Actions:
;   ISCC.exe /DAppVersion=1.25.0 /DSrc=..\build\TradingApp installer\TradingApp.iss
; Instalacja bez uprawnień administratora, do %LOCALAPPDATA%\Programs\TradingApp.
; Dane użytkownika (%LOCALAPPDATA%\TradingApp) nie są ruszane przy aktualizacji ani odinstalowaniu.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef Src
  #define Src "..\build\TradingApp"
#endif

[Setup]
AppId={{6F1C2B7E-3A54-4E2B-9C1D-7A0E5B8F2D41}
AppName=TradingApp
AppVersion={#AppVersion}
AppVerName=TradingApp {#AppVersion}
AppPublisher=TradingApp
AppPublisherURL=https://github.com/xboff57/tradingapp-demo
AppSupportURL=https://github.com/xboff57/tradingapp-demo/issues
AppUpdatesURL=https://github.com/xboff57/tradingapp-demo/releases
DefaultDirName={localappdata}\Programs\TradingApp
DefaultGroupName=TradingApp
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=TradingApp-Setup-{#AppVersion}
SetupIconFile=tradingapp.ico
UninstallDisplayIcon={app}\tradingapp.ico
UninstallDisplayName=TradingApp
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile=WAZNE.txt
InfoAfterFile=PO_INSTALACJI.txt
CloseApplications=no

[Languages]
Name: "pl"; MessagesFile: "compiler:Languages\Polish.isl"

[Tasks]
Name: "autostart"; Description: "Uruchamiaj TradingApp w tle po zalogowaniu do Windows (boty działają bez otwierania panelu)"; GroupDescription: "Praca w tle:"
Name: "nosleep"; Description: "Nie usypiaj komputera, gdy działa TradingApp (ekran może się wygaszać)"; GroupDescription: "Praca w tle:"
Name: "desktopicon"; Description: "Skrót na pulpicie"; GroupDescription: "Skróty:"

[Files]
Source: "{#Src}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "tradingapp.ico"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; stary kod aplikacji (żeby nie zostały pliki usunięte w nowej wersji); dane są gdzie indziej
Type: filesandordirs; Name: "{app}\app"

[Icons]
Name: "{group}\TradingApp"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.pyw"" --nie-usypiaj"; WorkingDir: "{app}"; IconFilename: "{app}\tradingapp.ico"; Tasks: nosleep
Name: "{group}\TradingApp"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\tradingapp.ico"; Tasks: not nosleep
Name: "{group}\Zatrzymaj TradingApp"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.pyw"" --stop"; WorkingDir: "{app}"; IconFilename: "{app}\tradingapp.ico"
Name: "{group}\TradingApp — wersja demo"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.pyw"" --demo"; WorkingDir: "{app}"; IconFilename: "{app}\tradingapp.ico"
Name: "{group}\Zatrzymaj wersję demo"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.pyw"" --demo --stop"; WorkingDir: "{app}"; IconFilename: "{app}\tradingapp.ico"
Name: "{group}\Folder danych i ustawień"; Filename: "{localappdata}\TradingApp\dane"
Name: "{group}\Instrukcja"; Filename: "{app}\INSTRUKCJA.html"
Name: "{group}\Odinstaluj TradingApp"; Filename: "{uninstallexe}"
Name: "{autodesktop}\TradingApp"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.pyw"" --nie-usypiaj"; WorkingDir: "{app}"; IconFilename: "{app}\tradingapp.ico"; Tasks: desktopicon and nosleep
Name: "{autodesktop}\TradingApp"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\tradingapp.ico"; Tasks: desktopicon and not nosleep
Name: "{userstartup}\TradingApp (w tle)"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.pyw"" --tlo --nie-usypiaj"; WorkingDir: "{app}"; IconFilename: "{app}\tradingapp.ico"; Tasks: autostart and nosleep
Name: "{userstartup}\TradingApp (w tle)"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.pyw"" --tlo"; WorkingDir: "{app}"; IconFilename: "{app}\tradingapp.ico"; Tasks: autostart and not nosleep

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.pyw"" --nie-usypiaj"; WorkingDir: "{app}"; Description: "Uruchom TradingApp teraz (otworzy się w przeglądarce)"; Flags: postinstall nowait skipifsilent; Tasks: nosleep
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; Description: "Uruchom TradingApp teraz (otworzy się w przeglądarce)"; Flags: postinstall nowait skipifsilent; Tasks: not nosleep

[UninstallRun]
Filename: "{app}\python\python.exe"; Parameters: """{app}\launcher.pyw"" --stop"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "StopApp"
Filename: "{app}\python\python.exe"; Parameters: """{app}\launcher.pyw"" --demo --stop"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "StopDemo"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
  Py: String;
begin
  Result := '';
  // aktualizacja: zatrzymaj działającą starszą wersję (dane zostają)
  Py := ExpandConstant('{app}\python\python.exe');
  if FileExists(Py) then
  begin
    Exec(Py, '"' + ExpandConstant('{app}\launcher.pyw') + '" --stop', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code);
    Exec(Py, '"' + ExpandConstant('{app}\launcher.pyw') + '" --demo --stop', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox('TradingApp został odinstalowany.' + #13#10#13#10 +
           'Twoje dane (boty, historia, konta, hasło) zostały w folderze:' + #13#10 +
           ExpandConstant('{localappdata}\TradingApp') + #13#10#13#10 +
           'Po ponownej instalacji wszystko wróci. Jeśli chcesz je usunąć na zawsze, skasuj ten folder ręcznie.',
           mbInformation, MB_OK);
end;
