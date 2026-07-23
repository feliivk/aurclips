"""Ingesta de contenido largo: canales de YouTube (yt-dlp) y carpeta local."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from .config import Config, ROOT, _exe_suffix
from .state import State

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts", ".flv"}


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


# Firmas de "no hay internet" en el stderr de yt-dlp. Sin esto, el mensaje al
# usuario sería el boilerplate de "report this issue on github" — que sugiere
# un bug cuando lo que pasa es que se cayó la red.
_NETWORK_SIGNS = ("unable to connect", "getaddrinfo failed", "connection refused",
                  "failed to establish a new connection",
                  "temporary failure in name resolution", "network is unreachable")


def _network_hint(stderr: str) -> str:
    """El pedazo útil del stderr de yt-dlp, o una línea clara si es la red."""
    lowered = stderr.lower()
    if any(sign in lowered for sign in _NETWORK_SIGNS):
        return "sin conexión a YouTube; se reintenta en la próxima corrida"
    return stderr.strip()[-300:] or "yt-dlp falló sin mensaje"


def _ytdlp_base(cfg: Config) -> list[str]:
    cmd = [sys.executable, "-m", "yt_dlp", "--ffmpeg-location", str(Path(cfg.ffmpeg).parent)]
    # runtime JS para resolver el "throttling" de YouTube (descargas rápidas):
    # el empaquetado en tools/ (nombre por SO) o, si no, uno del sistema
    bundled = ROOT / "tools" / "deno" / f"deno{_exe_suffix()}"
    deno_path = str(bundled) if bundled.exists() else shutil.which("deno")
    if deno_path:
        cmd += ["--js-runtimes", f"deno:{deno_path}"]
    return cmd


def probe_duration(cfg: Config, path: str) -> float | None:
    """Duración de un archivo de video en segundos, vía ffprobe."""
    try:
        r = _run([
            cfg.ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-of", "json", path,
        ])
    except OSError:  # ffprobe ausente (setup incompleto): degradar, no romper
        return None
    if r.returncode != 0:
        return None
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


def scan_inbox(cfg: Config, db: State) -> int:
    """Registra videos locales dejados en la carpeta inbox."""
    found = 0
    for f in sorted(cfg.inbox_dir.iterdir()):
        if f.suffix.lower() not in VIDEO_EXTS:
            continue
        source_id = str(f.resolve())
        if db.video_known(source_id):
            continue
        duration = probe_duration(cfg, source_id)
        # a diferencia de los canales, el inbox acepta cualquier duración:
        # los clips cortos se usan completos como Shorts
        if duration is not None and duration < 3:
            print(f"  [inbox] {f.name}: demasiado corto ({duration:.1f}s), se omite")
            db.add_video("local", source_id, f.stem, source_id, duration, skipped=True)
            continue
        db.add_video("local", source_id, f.stem, source_id, duration)
        print(f"  [inbox] nuevo: {f.name}")
        found += 1
    return found


def list_channel_videos(cfg: Config, channel_url: str) -> list[str]:
    """IDs de los videos más recientes de un canal (sin descargar)."""
    limit = cfg.get("channel_scan_limit", 5)
    r = _run(_ytdlp_base(cfg) + [
        "--flat-playlist", "--playlist-end", str(limit), "--print", "id", channel_url,
    ])
    if r.returncode != 0:
        print(f"  [error] no se pudo listar {channel_url}:\n{r.stderr.strip()[-500:]}")
        return []
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def download_video(cfg: Config, video_id: str,
                   require_min_duration: bool = True) -> tuple[str, str, float] | None:
    """Descarga un video de YouTube. Devuelve (ruta, título, duración) o None.

    El filtro de duración mínima protege el escaneo de canales (no bajar
    interludios de 30 s); una URL pasada a mano es una elección explícita del
    usuario y se respeta cualquier duración, como en el inbox.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    height = cfg.get("max_download_height", 1080)
    min_dur = cfg.get("min_source_duration", 300)
    match_filter = (f"duration >= {min_dur} & !is_live"
                    if require_min_duration else "!is_live")
    out_tmpl = str(cfg.downloads_dir / "%(id)s.%(ext)s")
    r = _run(_ytdlp_base(cfg) + [
        "-f", f"bv*[height<={height}]+ba/b[height<={height}]",
        "--merge-output-format", "mp4",
        "--match-filter", match_filter,
        "--no-playlist",
        "-o", out_tmpl,
        "--print", "after_move:filepath",
        "--print", "title",
        "--print", "duration",
        "--no-simulate", "--no-progress",
        url,
    ])
    if r.returncode != 0:
        # error real (red, YouTube caído...): NO es lo mismo que un video
        # filtrado a propósito — el llamador decide si reintenta después
        raise RuntimeError(
            f"descarga de {video_id} falló: {_network_hint(r.stderr)}")
    lines = [line for line in r.stdout.splitlines() if line.strip()]
    if len(lines) < 3:
        # el match-filter lo descartó (demasiado corto o en vivo): decisión,
        # no fallo — None significa exactamente eso
        return None
    # yt-dlp imprime title y duration en la etapa "video" y filepath al final
    title, duration, filepath = lines[0], lines[1], lines[-1]
    try:
        dur = float(duration)
    except ValueError:
        dur = probe_duration(cfg, filepath) or 0.0
    return filepath, title, dur


# ---------------------------------------------------------------------------
# El verbo de YouTube: URLs sueltas que se resuelven a archivos locales
# ---------------------------------------------------------------------------

def is_url(text: str) -> bool:
    """¿El argumento es una URL? Solo con esquema; lo demás es ruta o nombre."""
    return text.startswith(("http://", "https://"))


def _probe_url_id(cfg: Config, url: str) -> str | None:
    """El id del video detrás de la URL, sin descargar (una llamada rápida)."""
    r = _run(_ytdlp_base(cfg) + ["--no-playlist", "--print", "id", url])
    if r.returncode != 0:
        return None
    lines = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    return lines[-1] if lines else None


def find_downloaded(cfg: Config, video_id: str) -> Path | None:
    """La descarga ya hecha de este video, o None. Mismo patrón de nombre que
    las descargas de canales ({id}.ext), así ambas vías la comparten."""
    for f in sorted(cfg.downloads_dir.glob(f"{video_id}.*")):
        if f.suffix.lower() in VIDEO_EXTS:
            return f
    return None


def url_download(cfg: Config, url: str) -> Path:
    """Resuelve una URL a un archivo local, descargando una sola vez.

    No registra nada en la base: el material de una URL suelta es del
    recortador y el repaso, no del pipeline (ADR-0002). El sidecar de marcas
    caerá junto a la descarga, como con cualquier archivo.
    """
    video_id = _probe_url_id(cfg, url)
    if not video_id:
        raise ValueError(f"no pude leer la URL (¿es un video válido?): {url}")
    existing = find_downloaded(cfg, video_id)
    if existing is not None:
        print(f"  [url] ya descargado: {existing.name}")
        return existing
    print(f"  [url] descargando {url} ...")
    try:
        result = download_video(cfg, video_id, require_min_duration=False)
    except RuntimeError as e:  # error de descarga -> una línea en el CLI
        raise ValueError(str(e)) from e
    if result is None:  # solo puede ser el filtro !is_live
        raise ValueError(f"no se pudo descargar {url} (¿es una transmisión en vivo?)")
    path, title, duration = result
    print(f"  [url] listo: {title} ({duration:.0f}s)")
    return Path(path)


def check_channels(cfg: Config, db: State) -> int:
    """Busca videos nuevos en los canales configurados y los descarga."""
    channels = cfg.get("channels") or []
    found = 0
    for url in channels:
        print(f"  [canal] revisando {url}")
        for vid in list_channel_videos(cfg, url):
            if db.video_known(vid):
                continue
            print(f"  [canal] descargando {vid} ...")
            try:
                result = download_video(cfg, vid)
            except RuntimeError as e:
                # fallo transitorio (red): NO se registra — la próxima corrida
                # lo reintenta sola. Registrarlo como skipped lo enterraría
                # para siempre (skipped no lo reencola nadie).
                print(f"  [error] {e}")
                from .notify import notify
                notify(cfg, "error", f"Descarga de {vid} falló; se reintentará")
                continue
            if result is None:
                # filtrado a propósito (corto o en vivo): registrarlo como
                # omitido para no reevaluarlo cada corrida — esto sí es final
                db.add_video("youtube", vid, skipped=True)
                continue
            path, title, duration = result
            db.add_video("youtube", vid, title, path, duration)
            print(f"  [canal] nuevo: {title} ({duration:.0f}s)")
            found += 1
    return found


def ingest(cfg: Config, db: State) -> int:
    print("[1/4] Ingesta de contenido...")
    n = scan_inbox(cfg, db) + check_channels(cfg, db)
    print(f"  {n} video(s) nuevo(s)")
    return n
