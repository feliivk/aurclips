# Instalación inicial: entorno de Python, dependencias y ffmpeg.
# Ejecutar una sola vez:  powershell -ExecutionPolicy Bypass -File setup.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Write-Host "== Setup de aurclips ==" -ForegroundColor Cyan

# --- 1. Entorno virtual de Python (3.12 por compatibilidad con Whisper) ---
$venv = Join-Path $root ".venv"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    Write-Host "[1/2] Creando entorno virtual (.venv)..."
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { & py -3.12 -m venv $venv } else { & python -m venv $venv }
} else {
    Write-Host "[1/2] Entorno virtual ya existe"
}
$python = Join-Path $venv "Scripts\python.exe"

Write-Host "[1/2] Instalando dependencias (puede tardar unos minutos)..."
& $python -m pip install --upgrade pip --quiet
& $python -m pip install -r (Join-Path $root "requirements.txt") --quiet
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Write-Host "[1/2] GPU NVIDIA detectada: instalando librerias CUDA para Whisper..."
    & $python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 --quiet
}
Write-Host "[1/2] Dependencias listas"

# --- 1a. deno (runtime JS para descargas rapidas de yt-dlp) ---------------
$denoExe = Join-Path $root "tools\deno\deno.exe"
if (-not (Test-Path $denoExe)) {
    Write-Host "[1/2] Descargando deno (para descargas de YouTube sin limitacion)..."
    $zip = Join-Path $env:TEMP "deno.zip"
    Invoke-WebRequest -Uri "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip" -OutFile $zip
    New-Item -ItemType Directory -Force (Join-Path $root "tools\deno") | Out-Null
    Expand-Archive -Path $zip -DestinationPath (Join-Path $root "tools\deno") -Force
    Remove-Item $zip -Force
}

# --- 1b. Fuente Anton para los subtitulos ---------------------------------
$fontFile = Join-Path $root "tools\fonts\Anton-Regular.ttf"
if (-not (Test-Path $fontFile)) {
    New-Item -ItemType Directory -Force (Join-Path $root "tools\fonts") | Out-Null
    Invoke-WebRequest -Uri "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf" -OutFile $fontFile
    Write-Host "[1/2] Fuente Anton descargada"
}

# --- 2. ffmpeg ------------------------------------------------------------
$ffmpegExe = Join-Path $root "tools\ffmpeg\bin\ffmpeg.exe"
if (-not (Test-Path $ffmpegExe)) {
    Write-Host "[2/2] Descargando ffmpeg (~90 MB)..."
    $zip = Join-Path $env:TEMP "ffmpeg-release-essentials.zip"
    $tmp = Join-Path $env:TEMP "ffmpeg-extract"
    Invoke-WebRequest -Uri "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $zip
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $tmp
    $extracted = Get-ChildItem $tmp -Directory | Select-Object -First 1
    New-Item -ItemType Directory -Force (Join-Path $root "tools") | Out-Null
    Move-Item $extracted.FullName (Join-Path $root "tools\ffmpeg")
    Remove-Item $zip -Force
    Write-Host "[2/2] ffmpeg instalado en tools\ffmpeg"
} else {
    Write-Host "[2/2] ffmpeg ya existe"
}

Write-Host ""
Write-Host "Setup completo. Siguientes pasos:" -ForegroundColor Green
Write-Host "  1. Edita config.yaml: channel.angle y channel.title_examples (el"
Write-Host "     contexto con el que se escriben los titulos) y selection.profile"
Write-Host "  2. Credenciales de YouTube: pon client_secrets.json en credentials\ y corre:"
Write-Host "       .venv\Scripts\python -m aurclips auth"
Write-Host "  3. Prueba manual:  .venv\Scripts\python -m aurclips run"
Write-Host "  4. Revisa y aprueba: .venv\Scripts\python -m aurclips review"
Write-Host "  5. Automatiza:     powershell -File setup_task.ps1"
Write-Host ""
Write-Host "(Recomendado) Instala Ollama y corre 'ollama pull qwen2.5:7b': un"
Write-Host "modelo local escribe los titulos - sigue siendo 100% local y gratis."
Write-Host "Lee docs\grabar-en-beats.md: grabar marcando los momentos buenos es"
Write-Host "la mejora mas grande y no toca codigo."
