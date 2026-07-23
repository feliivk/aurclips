# Corrida diaria del bot en Windows (la usa el Programador de tareas).
# El log por corrida y su rotación los hace ahora el propio comando 'run',
# igual que en Linux/macOS — este wrapper solo lo invoca.
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
& (Join-Path $root ".venv\Scripts\python.exe") -m aurclips run
