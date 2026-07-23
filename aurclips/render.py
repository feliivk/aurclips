"""Render de clips con ffmpeg: corte, jump cuts (sin silencios), formato
vertical 9:16 y subtítulos virales quemados.

El recorte de pausas muertas (>1s) es una de las técnicas con más impacto en
retención según la investigación: el clip arranca en la primera palabra y no
deja momentos para que el espectador deslice.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .config import Config, ROOT
from .subtitles import build_ass

MAX_BLOCKS = 60      # tope de segmentos por clip (seguridad)
PAD_IN = 0.10        # aire antes de la primera palabra de un bloque
PAD_OUT = 0.25       # aire después de la última palabra de un bloque

# Como en subtitles.py: config.yaml ship estos mismos valores y un test lo
# comprueba, para que el respaldo del código no diga otra cosa que la config.
DEFAULT_SUBTITLES = True
DEFAULT_TIGHTEN_SILENCES = True
DEFAULT_MAX_PAUSE = 1.5
DEFAULT_FACE_TRACKING = True
DEFAULT_CRF = 20
DEFAULT_PRESET = "veryfast"


def safe_name(text: str, max_len: int = 60) -> str:
    text = re.sub(r"[^\w\sáéíóúüñÁÉÍÓÚÜÑ-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text.strip())
    return text[:max_len] or "clip"


def clip_paths(cfg: Config, clip_id: int, title: str,
               out_dir: Path | None = None,
               work_name: str | None = None) -> tuple[Path, Path]:
    """(carpeta de trabajo, ruta del mp4) de un clip. Crea ambas carpetas.

    Sin ``out_dir`` ni ``work_name`` produce las rutas del pipeline, que
    numera por id de clip. El modo recortador pasa las suyas: los recortes
    sueltos van a una carpeta por grabación y numeran por posición dentro de
    la corrida, así que un id repetido no pisa nada.
    """
    workdir = cfg.work_dir / (work_name or f"clip_{clip_id}")
    workdir.mkdir(parents=True, exist_ok=True)
    destination = out_dir or cfg.output_dir
    destination.mkdir(parents=True, exist_ok=True)
    return workdir, destination / f"{clip_id:04d}_{safe_name(title)}.mp4"


# Familia de respaldo si no hay ninguna fuente que copiar. libass la resuelve
# en los tres SO vía fontconfig/Core Text; "Arial Black" solo existía en Windows.
FALLBACK_FONT = "sans-serif"


def _font_source_dirs() -> tuple[Path, ...]:
    """Dónde buscar fuentes: primero la que viene con el paquete (Anton, OFL,
    portable en cualquier SO), luego la descargada a tools/ (compat checkout)."""
    return (Path(__file__).resolve().parent / "assets" / "fonts",
            ROOT / "tools" / "fonts")


def _resolve_font(cfg: Config, workdir: Path) -> str:
    """Copia las fuentes disponibles al workdir y devuelve el nombre a usar."""
    font = cfg.get("render.font", "Anton")
    copied = False
    for fonts_dir in _font_source_dirs():
        if not fonts_dir.is_dir():
            continue
        for pattern in ("*.tt[fc]", "*.otf"):
            for f in fonts_dir.glob(pattern):
                shutil.copy2(f, workdir / f.name)
                copied = True
    if font.lower() == "anton" and not copied:
        return FALLBACK_FONT  # respaldo si no hay ninguna fuente disponible
    return font


# ---------------------------------------------------------------------------
# Jump cuts: bloques de habla y remapeo de tiempos
# ---------------------------------------------------------------------------

def _speech_blocks(words: list[dict], clip_dur: float,
                   max_pause: float) -> list[tuple[float, float]]:
    """Intervalos [a,b) del clip que se conservan (el resto es silencio)."""
    blocks: list[list[float]] = []
    for w in words:
        if blocks and w["start"] - blocks[-1][1] <= max_pause:
            blocks[-1][1] = max(blocks[-1][1], w["end"])
        else:
            blocks.append([w["start"], w["end"]])
    padded: list[tuple[float, float]] = []
    for a, b in blocks:
        a = max(0.0, a - PAD_IN)
        b = min(clip_dur, b + PAD_OUT)
        if padded and a <= padded[-1][1]:
            padded[-1] = (padded[-1][0], max(padded[-1][1], b))
        else:
            padded.append((a, b))
    # si hay demasiados cortes, fusiona los huecos más chicos
    while len(padded) > MAX_BLOCKS:
        gaps = [(padded[i + 1][0] - padded[i][1], i) for i in range(len(padded) - 1)]
        _, i = min(gaps)
        padded[i] = (padded[i][0], padded[i + 1][1])
        del padded[i + 1]
    return padded


def _remap_words(words: list[dict],
                 blocks: list[tuple[float, float]]) -> list[dict]:
    """Traslada los tiempos de las palabras a la línea de tiempo ya recortada."""
    remapped = []
    offset = 0.0
    for a, b in blocks:
        for w in words:
            if a - 0.01 <= w["start"] < b:
                remapped.append({
                    "start": offset + (w["start"] - a),
                    "end": offset + min(w["end"], b) - a,
                    "word": w["word"],
                })
        offset += b - a
    return remapped


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render_clip(cfg: Config, video_path: str, start: float, end: float,
                title: str, words: list[dict], clip_id: int,
                out_dir: Path | None = None,
                work_name: str | None = None) -> Path:
    """Corta y renderiza un clip vertical 1080x1920. Devuelve la ruta del mp4.

    ``out_dir`` y ``work_name`` son para quien no es el pipeline: sin ellos
    las rutas son exactamente las de siempre (ver clip_paths).
    """
    workdir, out_path = clip_paths(cfg, clip_id, title, out_dir, work_name)
    duration = end - start

    # --- jump cuts -------------------------------------------------------
    tighten = cfg.get("render.tighten_silences", DEFAULT_TIGHTEN_SILENCES) and words
    blocks: list[tuple[float, float]] = [(0.0, duration)]
    render_words = words
    if tighten:
        max_pause = cfg.get("render.max_pause", DEFAULT_MAX_PAUSE)
        blocks = _speech_blocks(words, duration, max_pause)
        render_words = _remap_words(words, blocks)
        kept = sum(b - a for a, b in blocks)
        if kept < duration - 0.3:
            print(f"  [render] jump cuts: {duration:.1f}s -> {kept:.1f}s "
                  f"({len(blocks)} bloques de habla)")

    # --- encuadre (rostro o centrado) ------------------------------------
    crop_expr = "crop=ih*9/16:ih"
    if cfg.get("crop.face_tracking", DEFAULT_FACE_TRACKING):
        try:
            from .facecrop import face_crop_filter
            crop_expr = face_crop_filter(cfg, video_path, start, end)
        except Exception as e:  # noqa: BLE001 — el encuadre nunca rompe el render
            print(f"  [crop] detección de rostro falló ({e}); recorte centrado")

    # --- subtítulos ------------------------------------------------------
    vf_tail = [crop_expr, "scale=1080:1920"]
    if cfg.get("render.subtitles", DEFAULT_SUBTITLES) and render_words:
        font = _resolve_font(cfg, workdir)
        build_ass(render_words, cfg.get("render", {}) or {},
                  workdir / "subs.ass", font_name=font)
        # cwd=workdir evita el escapado de rutas de Windows; fontsdir=. usa
        # las fuentes copiadas al workdir
        vf_tail.append("subtitles=subs.ass:fontsdir=.")

    # --- comando ffmpeg --------------------------------------------------
    src = str(Path(video_path).resolve())
    common = [
        "-c:v", "libx264",
        "-preset", cfg.get("render.preset", DEFAULT_PRESET),
        "-crf", str(cfg.get("render.crf", DEFAULT_CRF)),
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
    ]
    simple = len(blocks) == 1 and blocks[0][0] < 0.02 and blocks[0][1] > duration - 0.02
    if simple:
        cmd = [
            cfg.ffmpeg, "-y",
            "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", src,
            "-vf", ",".join(vf_tail),
            *common, "-shortest", str(out_path.resolve()),
        ]
    else:
        parts, pairs = [], []
        for k, (a, b) in enumerate(blocks):
            parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},"
                         f"setpts=PTS-STARTPTS[v{k}]")
            parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},"
                         f"asetpts=PTS-STARTPTS[a{k}]")
            pairs.append(f"[v{k}][a{k}]")
        parts.append(f"{''.join(pairs)}concat=n={len(blocks)}:v=1:a=1[cv][ca]")
        parts.append(f"[cv]{','.join(vf_tail)}[vo]")
        cmd = [
            cfg.ffmpeg, "-y",
            "-ss", f"{start:.3f}", "-t", f"{duration + 0.5:.3f}", "-i", src,
            "-filter_complex", ";".join(parts),
            "-map", "[vo]", "-map", "[ca]",
            *common, str(out_path.resolve()),
        ]

    r = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg falló para el clip {clip_id}:\n{r.stderr[-800:]}")
    print(f"  [render] listo: {out_path.name}")
    return out_path
