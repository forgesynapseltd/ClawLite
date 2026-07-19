; ClawLite - Local-First Personal AI Assistant
; Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
; Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
; Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
; SPDX-License-Identifier: Apache-2.0
;
; installer/ClawLite.iss -- Script de Inno Setup que envuelve el build
; onedir de PyInstaller (dist/ClawLite/, ver ClawLite.spec) en un instalador
; .exe para el usuario final.
;
; PrivilegesRequired=lowest + instalación bajo {localappdata}\Programs:
; consistente con launcher.py:_set_data_home(), que ya usa %LOCALAPPDATA%
; para los datos del usuario -- el programa también se instala ahí, sin
; pedir permisos de administrador ni UAC, coherente con el resto del
; diseño "sin fricción" del instalador.
;
; AppMutex apunta al mismo nombre de mutex que launcher.py usa para su
; enforcement de instancia única (contrato estable, ver launcher.py) --
; así el instalador/desinstalador rechaza correr mientras ClawLite ya
; está en ejecución, en vez de pisar archivos en uso.
;
; El desinstalador (comportamiento por defecto de Inno Setup) solo borra
; lo que él mismo instaló bajo {app} -- nunca toca
; %LOCALAPPDATA%\ClawLite (carpeta de datos del usuario: .env, DB, vault),
; que vive fuera del árbol de instalación a propósito.
;
; MyAppVersion se lee del archivo VERSION en la raíz del repo -- misma
; fuente única de verdad que usa clawlite/_version.py -- para que el
; paquete Python y el instalador nunca queden desincronizados entre sí.

#define MyAppName "ClawLite"
#define MyAppVersion Trim(FileRead(FileOpen("..\VERSION")))
#define MyAppPublisher "FORGESYNAPSE LTD"
#define MyAppExeName "ClawLite.exe"
#define MyAppMutex "Global\FORGESYNAPSE.ClawLite.Launcher"

[Setup]
AppId={{AE8D6D4D-CD60-439A-BF38-B833827C3680}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist_installer
OutputBaseFilename=ClawLite-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
AppMutex={#MyAppMutex}
UninstallDisplayIcon={app}\{#MyAppExeName}
WizardStyle=modern

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\ClawLite\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
