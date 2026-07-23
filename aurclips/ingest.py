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


def download_video(cfg: Config, video_id: str) -> tuple[str, str, float] | None:
    """Descarga un video de YouTube. Devuelve (ruta, título, duración) o None."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    height = cfg.get("max_download_height", 1080)
    min_dur = cfg.get("min_source_duration", 300)
    out_tmpl = str(cfg.downloads_dir / "%(id)s.%(ext)s")
    r = _run(_ytdlp_base(cfg) + [
        "-f", f"bv*[height<={height}]+ba/b[height<={height}]",
        "--merge-output-format", "mp4",
        "--match-filter", f"duration >= {min_dur} & !is_live",
        "--no-playlist",
        "-o", out_tmpl,
        "--print", "after_move:filepath",
        "--print", "title",
        "--print", "duration",
        "--no-simulate", "--no-progress",
        url,
    ])
    if r.returncode != 0:
        print(f"  [error] descarga de {video_id} falló:\n{r.stderr.strip()[-500:]}")
        return None
    lines = [line for line in r.stdout.splitlines() if line.strip()]
    if len(lines) < 3:
        # el match-filter lo descartó (demasiado corto o en vivo)
        return None
    # yt-dlp imprime title y duration en la etapa "video" y filepath al final
    title, duration, filepath = lines[0], lines[1], lines[-1]
    try:
        dur = float(duration)
    except ValueError:
        dur = probe_duration(cfg, filepath) or 0.0
    return filepath, title, dur


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
            result = download_video(cfg, vid)
            if result is None:
                # registrarlo como omitido para no reintentar cada corrida
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
