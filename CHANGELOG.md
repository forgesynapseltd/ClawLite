# Changelog

Todas las versiones notables de ClawLite se documentan acá. Formato basado en
[Keep a Changelog](https://keepachangelog.com/), versionado según
[SemVer](https://semver.org/) (MAJOR.MINOR.PATCH).

La versión vigente vive en un único lugar: el archivo `VERSION` en la raíz del
repo. `clawlite/_version.py` y `installer/ClawLite.iss` lo leen directo — no
hay que actualizar el número en dos lugares.

## [0.2.0] — 2026-07-19

### Added
- Log en archivo real (`clawlite.log`, en la carpeta de datos del usuario,
  rotación 5MB/retención 3). Sin consola visible (`console=False`), no
  quedaba ningún rastro de qué pasaba si algo fallaba — este sink es la
  única fuente de verdad post-mortem disponible.
- Acceso directo "Reconfigurar ClawLite" en el Start Menu: reabre el
  asistente de configuración aunque ya esté todo configurado, con los 3
  campos (token de Telegram, clave de Tavily, ID de dueño) precargados
  con sus valores actuales para editar. Antes no había forma de volver a
  configurar sin borrar el `.env` a mano o reinstalar.

## [0.1.1] — 2026-07-19

### Fixed
- El instalador se caía al arrancar en Windows con `ValueError: Unable to
  configure formatter 'default'`. Causa: `uvicorn.Config()` configuraba por
  defecto el logging estándar de Python, cuyo formatter llama
  `sys.stdout.isatty()` — en un build empaquetado sin consola y sin
  redirección de salida (el escenario real de un usuario haciendo doble
  clic en el instalador), `sys.stdout` es `None`, y esa llamada rompía el
  proceso antes de poder loguear nada. Corregido pasando `log_config=None`
  a `uvicorn.Config()` (ClawLite ya usa loguru, no necesita el logging
  propio de uvicorn).

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
