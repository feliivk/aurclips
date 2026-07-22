"""Marcas del creador: lo que marcaste al grabar manda sobre la heurística.

La palanca más grande no está en el código sino arriba, en cómo grabas: si tú
señalas el momento, el selector no tiene que adivinarlo. Dos canales, ninguno
necesita sincronizar relojes:

- **Voz** — dices una frase gatillo mientras grabas ("esto es un short").
  Whisper ya la transcribió, así que la marca cae exactamente donde la dijiste,
  sin herramientas extra. Si el segmento es solo la frase, se silencia: marca
  el clip pero no entra en él.
- **Archivo** — un ``<video>.marks.txt`` junto al video, un tiempo por línea
  (``12:34``, ``1:02:03`` o segundos sueltos). Lo escribe ``aurclips mark`` o
  cualquier hotkey de tu grabadora que registre timestamps.

Una marca es un *ancla*: el instante que señalaste. Una ventana candidata
"cae en la marca" si el ancla queda dentro de ella (con holgura), así que da
igual si marcaste justo antes del beat (voz) o justo después (hotkey).
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PHRASES = (
    "esto es un short",
    "esto va al short",
    "marca aqui",
    "clip esto",
    "this is a short",
    "clip that",
)

# un segmento que solo es la frase gatillo se silencia; si dijiste la frase
# pegada al contenido, se conserva (perder texto real cuesta más que colar
# tres palabras de más)
MUTE_SLACK_CHARS = 12

# Nadie dice la frase igual dos veces, y Whisper tampoco la escribe igual
# siempre: "esto es short", "esto es un shot". El gatillo se compara por
# parecido, no por igualdad. 1.0 = exigir la frase literal.
DEFAULT_SIMILARITY = 0.85
NEAR_MISS = 0.15  # se avisa de lo que se quedó a esto del umbral
MAX_NOTES = 3     # sin inundar el log: solo las que más cerca estuvieron


@dataclass
class Marks:
    """Anclas del creador para un video y segmentos a silenciar."""

    anchors: list[float] = field(default_factory=list)
    muted_starts: set[float] = field(default_factory=set)
    by_voice: int = 0
    by_file: int = 0

    def __bool__(self) -> bool:
        return bool(self.anchors)

    def covers(self, start: float, end: float, tolerance: float) -> bool:
        """¿Alguna marca cae dentro de la ventana [start, end] (con holgura)?"""
        return any(start - tolerance <= a <= end + tolerance for a in self.anchors)


# ---------------------------------------------------------------------------
# Normalización y lectura
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Minúsculas, sin acentos y sin puntuación: 'Marca, aquí!' -> 'marca aqui'."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", text.lower())
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(re.findall(r"[a-z0-9]+", stripped))


def parse_timecode(raw: str) -> float | None:
    """Convierte '1:02:03', '12:34' o '754.5' a segundos. None si no se puede."""
    raw = raw.split("#", 1)[0].strip().replace(",", ".")
    if not raw:
        return None
    parts = raw.split(":")
    if len(parts) > 3:
        return None
    total = 0.0
    try:
        for part in parts:
            total = total * 60 + float(part)
    except ValueError:
        return None
    return total if total >= 0 else None


def sidecar_path(video_path: str | Path) -> Path:
    """``video.mp4`` -> ``video.marks.txt`` (mismo directorio)."""
    p = Path(video_path)
    return p.with_suffix("").with_name(p.stem + ".marks.txt")


def file_marks(video_path: str | Path) -> list[float]:
    """Marcas del sidecar ``<video>.marks.txt``; lista vacía si no existe."""
    path = sidecar_path(video_path)
    if not path.exists():
        return []
    times = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        t = parse_timecode(line)
        if t is not None:
            times.append(t)
    return sorted(times)


def _best_ratio(tokens: list[str], phrase: str) -> float:
    """Mejor parecido entre la frase y una ventana de palabras del segmento.

    Se prueban ventanas de una palabra menos, iguales y una más que la frase,
    para tolerar tanto que te comas una palabra como que metas una de más.
    """
    n = len(phrase.split())
    best = 0.0
    for size in {max(1, n - 1), n, n + 1}:
        for i in range(max(1, len(tokens) - size + 1)):
            window = " ".join(tokens[i:i + size])
            if window:
                best = max(best, difflib.SequenceMatcher(None, window, phrase).ratio())
    return best


def phrase_similarity(text: str, phrase: str) -> float:
    """Parecido crudo (0..1) entre el texto normalizado y la frase gatillo.

    Es el número con el que se calibra ``marks.similarity``, así que se expone
    sin recortar: el umbral se aplica fuera.
    """
    if phrase in text:
        return 1.0
    return _best_ratio(text.split(), phrase)


def match_phrase(text: str, phrase: str, threshold: float) -> float:
    """El parecido si pasa el umbral, 0.0 si no. Puerta, no medición.

    Así una marca no se pierde porque ese día dijeras "esto es short" en vez de
    "esto es un short". Para el número crudo, :func:`phrase_similarity`.
    """
    ratio = phrase_similarity(text, phrase)
    return ratio if ratio >= threshold else 0.0


def voice_marks(transcript: dict, phrases,
                similarity: float = DEFAULT_SIMILARITY
                ) -> tuple[list[float], set[float], list[str]]:
    """(anclas, inicios de segmento a silenciar, avisos de coincidencia difusa)."""
    wanted = [normalize(p) for p in phrases if normalize(p)]
    if not wanted:
        return [], set(), []
    anchors: list[float] = []
    muted: set[float] = set()
    notes: list[str] = []
    near: list[tuple[float, str]] = []
    for seg in transcript.get("segments", []):
        text = normalize(seg.get("text", ""))
        if not text:
            continue
        hit, ratio = "", 0.0
        for phrase in wanted:
            score = phrase_similarity(text, phrase)
            if score > ratio:
                hit, ratio = phrase, score
        said = seg.get("text", "").strip()
        if ratio < similarity:
            # el falso negativo es el caso que importa calibrar: si dijiste la
            # frase y no marcó, aquí está el número exacto que le faltó
            if hit and ratio >= similarity - NEAR_MISS:
                near.append((ratio, f'casi marca (no contó): "{said}" ≈ "{hit}" '
                                    f'({ratio:.0%}, umbral {similarity:.0%})'))
            continue
        if ratio < 1.0:  # visible a propósito: una marca difusa se revisa
            notes.append(f'entró por parecido: "{said}" ≈ "{hit}" ({ratio:.0%})')
        if len(text) <= len(hit) + MUTE_SLACK_CHARS:
            # el segmento es solo la marca: el beat empieza al terminarla
            anchors.append(float(seg["end"]))
            muted.add(float(seg["start"]))
        else:
            # la dijiste pegada al contenido: el beat arranca ahí mismo
            anchors.append(float(seg["start"]))
    # Los casi-marca solo se avisan si el video quedó SIN ninguna marca: ahí un
    # falso negativo es el sospechoso obvio y el aviso trae el número exacto
    # para recalibrar. Si ya marcaste bien, avisar de cada "esto es un
    # problema" a 0.77 sería ruido que enseña a ignorar los avisos.
    if not anchors:
        near.sort(key=lambda n: n[0], reverse=True)
        notes += [text for _, text in near[:MAX_NOTES]]
    return sorted(anchors), muted, notes


def load_marks(cfg, video_path: str | Path, transcript: dict) -> Marks:
    """Todas las marcas de un video (voz + archivo), según la config."""
    if not cfg.get("marks.enabled", True):
        return Marks()
    phrases = cfg.get("marks.phrases") or DEFAULT_PHRASES
    similarity = cfg.get("marks.similarity", DEFAULT_SIMILARITY)
    voice, muted, notes = voice_marks(transcript, phrases, similarity)
    from_file = file_marks(video_path)
    marks = Marks(anchors=sorted(voice + from_file), muted_starts=muted,
                  by_voice=len(voice), by_file=len(from_file))
    if marks:
        detail = []
        if marks.by_voice:
            detail.append(f"{marks.by_voice} por voz")
        if marks.by_file:
            detail.append(f"{marks.by_file} del archivo")
        print(f"  [marcas] {len(marks.anchors)} marca(s) tuyas ({', '.join(detail)})")
    # los avisos se imprimen aunque no haya marcas: el caso que hay que ver es
    # justamente "dije la frase y este video salió sin marcar"
    for note in notes:
        print(f"  [marcas] {note}")
    return marks


# ---------------------------------------------------------------------------
# Sesión de marcado en vivo (comando `mark`)
# ---------------------------------------------------------------------------

def record_session(cfg, name: str | None = None) -> Path:
    """Sesión interactiva: cada Enter marca el instante actual.

    Arráncala en el mismo momento en que empiezas a grabar; los tiempos son
    relativos a ese arranque. Al terminar escribe el sidecar en el inbox, para
    que al dejar ahí la grabación con el mismo nombre el selector la encuentre.
    """
    import time
    from datetime import datetime

    name = (name or datetime.now().strftime("grabacion-%Y%m%d-%H%M")).strip()
    target = cfg.inbox_dir / f"{name}.marks.txt"

    print(f"Marcando para: {name}")
    print("  Enter  -> marca este instante")
    print("  Ctrl+C -> terminar y guardar")
    print("(arranca a grabar AHORA: el tiempo cero es este momento)\n")

    t0 = time.monotonic()
    times: list[float] = []
    try:
        while True:
            input()
            t = time.monotonic() - t0
            times.append(t)
            print(f"  marca {len(times):>2} en {int(t) // 60:02d}:{int(t) % 60:02d}")
    except (KeyboardInterrupt, EOFError):
        print()

    # ASCII puro: el sidecar se abre y edita en cualquier editor de Windows
    lines = [f"# marcas de {name} - generadas por aurclips mark"]
    lines += [f"{int(t) // 60:02d}:{int(t) % 60:02d}" for t in times]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(times)} marca(s) guardadas en {target}")
    print(f"Deja tu grabación como {cfg.inbox_dir / (name + '.mp4')} y corre 'run'.")
    return target
