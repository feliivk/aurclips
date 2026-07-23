"""Transporte con mpv para el repaso: lanzar, preguntar la posición, cerrar.

Delgado a propósito y sin tests automáticos — el mismo trato que ffmpeg: aquí
no hay lógica, solo el cable. La lógica del repaso vive en marks.MarkingSession
y se prueba sin nada de esto.

mpv es el único reproductor multiplataforma con un IPC limpio para preguntar
``playback-time``: un named pipe en Windows, un unix socket en POSIX, JSON por
línea en ambos. Es dependencia opcional solo de este modo.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .config import ROOT, _exe_suffix

# Teclas que marcan DENTRO del reproductor. La primera prueba real lo dejó
# claro: quien repasa tiene las manos y los ojos en el video, no en el
# terminal — los Enter iban a la ventana de mpv y se perdían. Se le piden a
# mpv por IPC (comando keybind -> script-message) y llegan como eventos
# client-message; las mismas teclas que en el terminal.
KEY_BINDINGS = (
    ("ENTER", "aurclips-mark"),
    ("u", "aurclips-undo"),
)


def find_mpv() -> str:
    """Ruta de mpv, o un error de una línea con la instalación por SO.

    Como los demás binarios del repo: primero el empaquetado en tools/ (la
    convención de Windows, donde también viven ffmpeg y deno), luego el PATH.
    """
    bundled = ROOT / "tools" / "mpv" / f"mpv{_exe_suffix()}"
    if bundled.exists():
        return str(bundled)
    found = shutil.which("mpv")
    if found:
        return found
    raise FileNotFoundError(
        "No se encontró mpv (el reproductor del repaso). Instálalo y agrégalo "
        "al PATH (macOS: brew install mpv · Linux: apt install mpv · "
        "Windows: winget install mpv), o deja el portable en tools/mpv."
    )


class MpvPlayer:
    """Una instancia de mpv reproduciendo un video, con su socket de IPC."""

    def __init__(self, mpv_path: str, video_path: str | Path):
        if os.name == "nt":
            self._ipc = rf"\\.\pipe\aurclips-mpv-{os.getpid()}"
        else:
            self._ipc = str(Path(tempfile.gettempdir()) / f"aurclips-mpv-{os.getpid()}.sock")
        # stdin=DEVNULL: el terminal es de la sesión de marcado; sin esto mpv
        # heredaría la consola y competiría con el hilo lector por las teclas
        self._proc = subprocess.Popen(
            [mpv_path, f"--input-ipc-server={self._ipc}", "--", str(video_path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._events: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._event_pump, daemon=True).start()

    def poll_event(self, timeout: float) -> str | None:
        """'mark' o 'undo' si el usuario pulsó la tecla en el reproductor."""
        try:
            return self._events.get(timeout=timeout)
        except queue.Empty:
            return None

    def show_message(self, text: str) -> None:
        """OSD en el reproductor: el eco de la marca, donde están los ojos."""
        try:
            self._roundtrip(json.dumps({"command": ["show-text", text, 1500]}) + "\n")
        except OSError:
            pass

    def _event_pump(self) -> None:
        """Hilo de eventos: registra las teclas en mpv y escucha sus pulsaciones.

        Conexión propia y persistente (el resto de peticiones va por conexiones
        cortas aparte, así no se entrelazan). Muere sola cuando mpv se cierra.
        """
        conn = self._connect_for_events()
        if conn is None:
            return
        write, read, close = conn
        try:
            for key, message in KEY_BINDINGS:
                write((json.dumps({"command": ["keybind", key,
                       f"script-message {message}"]}) + "\n").encode("utf-8"))
            buffer = b""
            while True:
                data = read()
                if not data:  # mpv cerró la conexión
                    return
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if payload.get("event") == "client-message":
                        args = payload.get("args") or []
                        if args and str(args[0]).startswith("aurclips-"):
                            self._events.put(str(args[0]).removeprefix("aurclips-"))
        except OSError:
            return
        finally:
            try:
                close()
            except OSError:
                pass

    def _connect_for_events(self):
        """(write, read, close) sobre el IPC, esperando a que mpv lo cree."""
        for _ in range(40):  # mpv tarda un instante en crear el socket/pipe
            if not self.alive():
                return None
            try:
                if os.name == "nt":
                    pipe = open(self._ipc, "r+b", buffering=0)
                    return pipe.write, (lambda: pipe.read(4096)), pipe.close
                import socket
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(self._ipc)
                return sock.sendall, (lambda: sock.recv(4096)), sock.close
            except OSError:
                time.sleep(0.25)
        return None

    def alive(self) -> bool:
        return self._proc.poll() is None

    def playback_time(self) -> float | None:
        """Posición de reproducción en segundos; None si no se pudo leer."""
        request = json.dumps({"command": ["get_property", "playback-time"]}) + "\n"
        try:
            for line in self._roundtrip(request):
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "error" in payload:  # respuesta a la petición (no un evento)
                    if payload["error"] == "success" and isinstance(
                            payload.get("data"), (int, float)):
                        return float(payload["data"])
                    return None
        except OSError:
            return None
        return None

    def _roundtrip(self, request: str) -> list[str]:
        """Envía una petición y devuelve las líneas de respuesta."""
        if os.name == "nt":
            # el pipe lo crea mpv; abrir/escribir/leer por petición es lo robusto
            with open(self._ipc, "r+b", buffering=0) as pipe:
                pipe.write(request.encode("utf-8"))
                return pipe.read(4096).decode("utf-8", "replace").splitlines()
        import socket
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            sock.connect(self._ipc)
            sock.sendall(request.encode("utf-8"))
            chunks = b""
            while b"\n" not in chunks:
                data = sock.recv(4096)
                if not data:
                    break
                chunks += data
            return chunks.decode("utf-8", "replace").splitlines()

    def close(self) -> None:
        if self.alive():
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
