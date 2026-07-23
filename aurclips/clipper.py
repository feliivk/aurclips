"""Modo recortador: recortes sueltos, sin pipeline.

Una grabación entra, salen videos verticales con subtítulos y su metadata al
lado. No se abre la base, no se piden credenciales, no queda cola pendiente:
un recorte suelto no tiene progreso ni criterio y no consume hueco de
publicación (ver CONTEXT.md).

Es un envoltorio, no un pipeline paralelo: transcribe, selecciona y renderiza
con las mismas piezas y las mismas reglas que `process`. Lo único que cambia
es de dónde salen los textos ya aceptados con que se comparan los duplicados
(aquí, la propia corrida) y dónde caen los archivos.
"""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .render import render_clip, safe_name
from .safety import screen_clip
from .select_clips import Clip, clip_words, select_clips
from .transcribe import transcribe


def plan_clips(cfg: Config, transcript: dict, title: str,
               video_path: str | Path) -> list[Clip]:
    """Los clips que sobreviven a la selección y a los filtros de calidad.

    La política es literalmente la misma que la del pipeline (screen_clip); lo
    único que cambia es contra qué se comparan los duplicados — aquí solo
    contra los recortes ya aceptados en esta corrida, porque no hay base que
    recuerde las anteriores.
    """
    clips = select_clips(cfg, transcript, title, str(video_path))
    kept: list[Clip] = []
    accepted: list[tuple[int, str]] = []
    for clip in clips:
        text = " ".join(w["word"] for w in clip_words(transcript, clip.start, clip.end))
        verdict = screen_clip(cfg, text, accepted)
        if verdict.unsafe_terms:
            print(f"  [filtro] {clip.title!r} contiene: "
                  f"{', '.join(verdict.unsafe_terms[:5])} -> "
                  f"{cfg.get('safety.action', 'skip')}")
        if verdict.duplicate_of is not None:
            print(f"  [dedup] {clip.title!r} es casi idéntico al recorte "
                  f"#{verdict.duplicate_of}; se omite")
        if not verdict.keep:
            continue
        kept.append(clip)
        accepted.append((len(kept), text))
    return kept


def metadata_text(clip: Clip) -> str:
    """Título, descripción y hashtags de un recorte, listos para copiar y pegar."""
    blocks = [clip.title.strip()]
    if clip.description.strip():
        blocks.append(clip.description.strip())
    if clip.tags:
        blocks.append(" ".join(f"#{tag.lstrip('#')}" for tag in clip.tags))
    return "\n\n".join(blocks) + "\n"


def write_metadata(clip: Clip, clip_path: Path) -> Path:
    """Deja la metadata del recorte junto a su mp4, con el mismo nombre base."""
    path = clip_path.with_suffix(".txt")
    path.write_text(metadata_text(clip), encoding="utf-8")
    return path


def clip_recording(cfg: Config, video_path: str | Path,
                   out_dir: str | Path | None = None,
                   max_clips: int | None = None) -> list[Path]:
    """Recorta una grabación y devuelve las rutas de los mp4 producidos."""
    path = Path(video_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"no existe la grabación: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"no es un archivo de video: {path}")

    if max_clips is not None:
        cfg.override("selection.clips_per_video", max_clips)

    title = path.stem
    slug = safe_name(title)
    destination = Path(out_dir) if out_dir else cfg.output_dir / slug
    if destination.resolve() == cfg.output_dir.resolve():
        # los recortes sueltos numeran por posición en la corrida y los clips
        # del pipeline por id: en la misma carpeta acabarían pisándose
        raise ValueError(
            f"{destination} es la carpeta del pipeline; los recortes sueltos "
            f"necesitan la suya (quita --out o apunta a otra)")

    print(f"[1/3] Transcribiendo: {title}")
    transcript = transcribe(cfg, str(path))

    print(f"[2/3] Seleccionando recortes: {title}")
    clips = plan_clips(cfg, transcript, title, path)
    if not clips:
        print("  sin recortes útiles en esta grabación")
        return []

    print(f"[3/3] Renderizando {len(clips)} recorte(s) en {destination}")
    outputs: list[Path] = []
    for position, clip in enumerate(clips, start=1):
        words = clip_words(transcript, clip.start, clip.end)
        mp4 = render_clip(cfg, str(path), clip.start, clip.end, clip.title,
                          words, position, out_dir=destination,
                          work_name=f"suelto_{slug}_{position}")
        write_metadata(clip, mp4)
        outputs.append(mp4)

    print(f"\n{len(outputs)} recorte(s) en {destination}")
    for mp4, clip in zip(outputs, clips):
        star = " ★ marcado por ti" if clip.marked else ""
        print(f"  {mp4.name}{star}")
        print(f"    {clip.title}")
    return outputs
