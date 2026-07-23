#!/usr/bin/env sh
# Instalación de aurclips en Linux y macOS.  (En Windows usa setup.ps1.)
# Uso:  sh setup.sh
set -e
root="$(cd "$(dirname "$0")" && pwd)"

echo "== Setup de aurclips =="

# --- 1. Entorno virtual de Python 3.12 -------------------------------------
if [ ! -d "$root/.venv" ]; then
    echo "[1/2] Creando entorno virtual (.venv)..."
    py="$(command -v python3.12 || command -v python3 || command -v python)"
    if [ -z "$py" ]; then
        echo "No encontré Python. Instala Python 3.12 y reintenta." >&2
        exit 1
    fi
    "$py" -m venv "$root/.venv"
else
    echo "[1/2] Entorno virtual ya existe"
fi
python="$root/.venv/bin/python"

echo "[1/2] Instalando aurclips y dependencias..."
"$python" -m pip install --upgrade pip --quiet
"$python" -m pip install -e "$root" --quiet

# GPU NVIDIA: solo Linux x86_64 (en macOS no hay CUDA)
if command -v nvidia-smi >/dev/null 2>&1; then
    echo "[1/2] GPU NVIDIA detectada. Para acelerar la transcripción:"
    echo "        $python -m pip install -e \"$root\"'[cuda]'"
fi

# --- 2. ffmpeg del sistema -------------------------------------------------
if command -v ffmpeg >/dev/null 2>&1; then
    echo "[2/2] ffmpeg encontrado: $(command -v ffmpeg)"
else
    echo "[2/2] Falta ffmpeg. Instálalo con tu gestor de paquetes:"
    echo "        macOS:          brew install ffmpeg"
    echo "        Debian/Ubuntu:  sudo apt install ffmpeg"
    echo "        Fedora:         sudo dnf install ffmpeg"
fi

# deno es opcional (acelera descargas de YouTube); si no está, se degrada solo.

echo ""
echo "Setup completo. Siguientes pasos:"
echo "  1. Edita config.yaml (channel.angle, selection.profile)."
echo "  2. Prueba el recortador:   $root/.venv/bin/aurclips clip mi_video.mp4"
echo "  3. (Opcional) Ollama:      ollama pull qwen2.5:7b"
echo "  4. Automatiza la corrida diaria: mira packaging/ (cron, systemd, launchd)."
