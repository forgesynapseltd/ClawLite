# Changelog

Todas las versiones notables de ClawLite se documentan acá. Formato basado en
[Keep a Changelog](https://keepachangelog.com/), versionado según
[SemVer](https://semver.org/) (MAJOR.MINOR.PATCH).

La versión vigente vive en un único lugar: el archivo `VERSION` en la raíz del
repo. `clawlite/_version.py` y `installer/ClawLite.iss` lo leen directo — no
hay que actualizar el número en dos lugares.

## [0.1.0] — 2026-07-19

Primera versión documentada del instalador de un clic (PyInstaller + Inno
Setup). Incluye:

- Wizard web local (`setup_web.py`) con detección funcional de Docker/Ollama,
  configuración guiada, y captura automática de `TELEGRAM_OWNER_ID` vía la
  API real de Telegram (con respaldo manual).
- Launcher único (`launcher.py`) con instancia única (mutex de Windows) y
  arranque directo al bot cuando la configuración ya está completa.
- Instalador Inno Setup: instalación sin privilegios de administrador,
  acceso directo de Start Menu, desinstalador que nunca toca los datos del
  usuario, soporte de upgrade (mismo `AppId` entre versiones).
