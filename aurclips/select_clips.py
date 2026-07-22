"""Selección de highlights, 100% local.

El motor es uno solo y es honesto: la heurística de ``heuristics.py`` puntúa
ventanas y se queda con las mejores. Las dos palancas de verdad están fuera:

- **arriba**, tus marcas al grabar (``marks.py``), que mandan sobre cualquier
  puntuación;
- **abajo**, la redacción del título (``titles.py``) y lo que digan las
  métricas de lo publicado (``stats.py``).

El LLM local ya no elige clips: solo redacta. Un modelo de 7B no sabe qué le
funciona a tu canal, y fingir que sí complicaba el centro del pipeline sin
mejorar la elección.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel

from .config import Config
from .heuristics import Candidate, find_candidates, make_metadata
from .ingest import probe_duration
from .marks import load_marks


class Clip(BaseModel):
    start_s: float
    end_s: float
    title: str
    description: str
    hashtags: List[str]
    score: float = 0.0
    marked: bool = False
    text: str = ""  # transcripción completa del clip (contexto para el titulador)


def _write_metadata(cfg: Config, clips: list[Clip], video_title: str) -> None:
    """Reescribe título/descripción/hashtags con el LLM local, si está."""
    from . import titles

    if not clips or not titles.enabled(cfg):
        return
    model = cfg.get("titles.model", titles.DEFAULT_MODEL)
    print(f"  [títulos] redactando con {model} ({len(clips)} clip(s))...")
    for clip in clips:
        proposal = titles.propose(cfg, clip.text, video_title)
        if proposal is None:
            continue  # el respaldo heurístico ya está puesto
        title, description, hashtags = proposal
        clip.title = title
        if description:
            clip.description = description
        if hashtags:
            clip.hashtags = hashtags


def select_clips(cfg: Config, transcript: dict, video_title: str,
                 video_path: str) -> list[Clip]:
    n = cfg.get("selection.clips_per_video", 3)
    max_s = cfg.get("selection.max_clip_seconds", 59)
    if cfg.get("selection.engine") is not None:
        print("  [aviso] 'selection.engine' ya no se usa: el LLM local ahora "
              "solo redacta títulos (clave 'titles.engine' en config.yaml)")

    # video que ya cabe como Short -> se usa completo, sin recortar
    segs = [s for s in transcript["segments"] if s["words"]]
    total = probe_duration(cfg, video_path) or (segs[-1]["end"] if segs else 0)
    if total and total <= max_s + 2:
        if not segs:
            print("  [selector] clip corto sin voz detectada; se omite")
            return []
        cand = Candidate(0.0, total, 1.0, segs)
        print(f"  [selector] video corto ({total:.0f}s): se usa completo como un Short")
        clips = _to_clips(cfg, [cand], video_title)
        return clips

    marks = load_marks(cfg, video_path, transcript)

    # número objetivo adaptativo: ~1 Short por cada minutes_per_short minutos
    # de video, acotado entre 1 y clips_per_video (que actúa de tope)
    cap = n
    mps = cfg.get("selection.minutes_per_short", 4)
    if total and mps:
        n = max(1, min(n, int(total / (mps * 60))))
    if marks:
        # marcaste a propósito: la densidad automática no manda sobre eso
        n = cap

    # la heurística genera las candidatas con margen para el filtro de calidad
    # (nunca menos que tus marcas: lo que señalaste no se descarta callando)
    limit = max(n * 2, 6, len(marks.anchors))
    candidates = find_candidates(cfg, transcript, video_path, limit, marks)
    if not candidates:
        print("  [selector] no se encontraron ventanas útiles")
        return []

    # descarte por calidad relativa: mejor pocos Shorts buenos que rellenar
    # el cupo con candidatos muy por debajo del mejor del propio video
    # (lo que marcaste queda exento: ahí decidiste tú)
    floor = cfg.get("selection.quality_floor", 0.55)
    best = max(c.score for c in candidates)
    if floor:
        if best > 0:
            kept = [c for c in candidates if c.marked or c.score >= floor * best]
        else:
            # todo el campo es flojo (puntuaciones <= 0, posible con audio
            # real): la fracción del mejor pierde sentido; conservar solo el
            # mejor en vez de rellenar el cupo con lo peor
            kept = [c for c in candidates if c.marked] or \
                   [max(candidates, key=lambda c: c.score)]
        if len(kept) < len(candidates):
            print(f"  [selector] {len(candidates) - len(kept)} candidato(s) "
                  f"descartados por calidad (umbral {floor:.2f} del mejor)")
        candidates = kept

    marked = sum(1 for c in candidates if c.marked)
    if marked > n:
        print(f"  [marcas] {marked} momentos marcados y el tope son {n}; "
              f"sube 'selection.clips_per_video' si quieres todos")

    # se publican los mejores, no los primeros: lo que marcaste va delante y
    # después manda la puntuación (el orden cronológico se restaura al final)
    candidates.sort(key=lambda c: (c.marked, c.score), reverse=True)
    clips = _to_clips(cfg, candidates[:n], video_title)
    clips.sort(key=lambda c: c.start_s)
    print(f"  [selector] {len(clips)} clip(s) seleccionados")
    for c in clips:
        flag = " ★" if c.marked else ""
        print(f"    - [{c.start_s:.0f}s-{c.end_s:.0f}s]{flag} {c.title}")
    return clips


def _to_clips(cfg: Config, candidates: list[Candidate],
              video_title: str) -> list[Clip]:
    """Candidatas -> Clips con metadata heurística y, si hay, del LLM local."""
    clips: list[Clip] = []
    used: set[str] = set()
    for cand in candidates:
        title, description, hashtags = make_metadata(cand, used)
        used.add(title.lower())
        # el LLM recibe la transcripción completa del clip, no la primera frase
        clips.append(Clip(start_s=cand.start, end_s=cand.end, title=title,
                          description=description, hashtags=hashtags,
                          score=cand.score, marked=cand.marked,
                          text=cand.text.strip()))
    _write_metadata(cfg, clips, video_title)
    return clips


def clip_words(transcript: dict, start: float, end: float) -> list[dict]:
    """Palabras (con tiempos relativos al clip) dentro de [start, end]."""
    words = []
    for seg in transcript["segments"]:
        if seg["end"] < start or seg["start"] > end:
            continue
        for w in seg["words"]:
            if w["start"] >= start - 0.05 and w["end"] <= end + 0.05:
                words.append({
                    "start": max(0.0, w["start"] - start),
                    "end": max(0.0, w["end"] - start),
                    "word": w["word"],
                })
    return words
