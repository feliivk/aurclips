"""Marcas del creador: lo que marcaste al grabar manda sobre la heurística.

La palanca más grande no está en el código sino arriba, en cómo grabas: si tú
señalas el momento, el selector no tiene que adivinarlo. Tres canales, ninguno
necesita sincronizar relojes:

- **Voz** — dices una frase gatillo mientras grabas ("esto es un short").
  Whisper ya la transcribió, así que la marca cae exactamente donde la dijiste,
  sin herramientas extra. Si el segmento es solo la frase, se silencia: marca
  el clip pero no entra en él.
- **Archivo** — un ``<video>.marks.txt`` junto al video, un tiempo por línea
  (``12:34``, ``1:02:03`` o segundos sueltos). Lo escribe ``aurclips mark`` o
  cualquier hotkey de tu grabadora que registre timestamps.
- **Repaso** — ves la grabación después (``aurclips mark <video>``) y cada
  Enter marca el momento que está sonando. Escribe el mismo sidecar.

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

# Nadie dice el gatillo igual dos veces. Ante un fraseo que no marcó, la
# respuesta es AGREGARLO AQUÍ, no bajar `marks.similarity`: una variante nueva
# se vuelve coincidencia exacta sin acercar el umbral a los falsos positivos.
DEFAULT_PHRASES = (
    "esto es un short",
    "esto va a ser un short",
    "esto va al short",
    "este es el short",
    "va para short",
    "marca aqui",
    "clip esto",
    "this is a short",
    "clip that",
)

# La similitud de caracteres no distingue afirmar de negar: "esto no es un
# short" se parece un 91% a "esto es un short" siendo justo lo contrario, y
# ningún umbral separa eso (la variante legítima puntúa MENOS que la negación).
# Lo que dos letras no pueden decidir, una palabra sí.
NEGATIONS = {"no", "nunca", "tampoco", "jamas", "nada"}

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


def _best_window(tokens: list[str], phrase: str) -> tuple[float, int, int]:
    """(parecido, inicio, tamaño) del trozo del segmento más parecido a la frase.

    Se prueban ventanas de una palabra menos, iguales y una más que la frase,
    para tolerar tanto que te comas una palabra como que metas una de más.
    """
    n = len(phrase.split())
    best = (0.0, 0, n)
    for size in {max(1, n - 1), n, n + 1}:
        for i in range(max(1, len(tokens) - size + 1)):
            window = " ".join(tokens[i:i + size])
            if window:
                ratio = difflib.SequenceMatcher(None, window, phrase).ratio()
                if ratio > best[0]:
                    best = (ratio, i, size)
    return best


def trigger_match(text: str, phrase: str) -> tuple[float, bool]:
    """(parecido crudo 0..1, ¿está negada?) del texto normalizado vs la frase.

    La negación no se busca a una distancia fija sino en una **dirección**: en
    español niega hacia adelante, así que cuenta todo lo que va desde el inicio
    de la frase hasta el final de la ventana que coincidió. "Esto nunca va a
    ser un short" queda descartado aunque el "nunca" caiga tres palabras antes
    de la coincidencia; "esto es un short, no te lo pierdas" marca igual,
    porque ahí el "no" viene después. La regla vale también cuando la frase
    coincide literal: decir el gatillo exacto no salta el guard, o "nada de
    esto va para short" marcaría al 100%.
    """
    phrase_tokens = phrase.split()
    tokens = text.split()
    exact = text.find(phrase)
    if exact >= 0:  # dicha literal: el alcance es lo que va antes de la frase
        ratio, scope = 1.0, text[:exact].split()
    else:
        ratio, start, size = _best_window(tokens, phrase)
        scope = tokens[:start + size]
    negated = (any(w in NEGATIONS for w in scope)
               and not any(w in NEGATIONS for w in phrase_tokens))
    return ratio, negated


def phrase_similarity(text: str, phrase: str) -> float:
    """Parecido crudo, sin recortar: el número con el que se calibra el umbral.

    Ignora la negación a propósito — es la medición, no la decisión. Para
    decidir, :func:`match_phrase`.
    """
    return trigger_match(text, phrase)[0]


def match_phrase(text: str, phrase: str, threshold: float) -> float:
    """El parecido si dispara el gatillo, 0.0 si no. Puerta, no medición.

    Así una marca no se pierde porque ese día dijeras "esto es short" en vez de
    "esto es un short", y una negación no marca por parecerse.
    """
    ratio, negated = trigger_match(text, phrase)
    return 0.0 if negated or ratio < threshold else ratio


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
        hit, ratio, blocked = "", 0.0, ""
        for phrase in wanted:
            score, negated = trigger_match(text, phrase)
            if negated:  # negar no es marcar, por mucho que se parezca
                if score >= similarity and not blocked:
                    blocked = phrase
                continue
            if score > ratio:
                hit, ratio = phrase, score
        said = seg.get("text", "").strip()
        if blocked and not hit:
            near.append((similarity, f'descartada por negación: "{said}" '
                                     f'(se parece a "{blocked}" pero la niega)'))
            continue
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
# Lógica de sesión: eventos de marcado -> contenido del sidecar
# ---------------------------------------------------------------------------

# Dos Enter a menos de esto (s) son el mismo momento: el nervioso no duplica.
MIN_MARK_GAP = 1.0


def _mmss(t: float) -> str:
    """MM:SS para mostrar en pantalla (los segundos enteros bastan al ojo)."""
    return f"{int(t) // 60:02d}:{int(t) % 60:02d}"


def _timecode(t: float) -> str:
    """El tiempo como se escribe en el sidecar: MM:SS, con decimales solo si
    los hay. Así una marca hecha en pausa conserva su precisión y una marca
    preexistente con fracción (`90.5`) no se redondea al reescribir el archivo
    — conservar es conservar. `parse_timecode` lee ambas formas."""
    minutes, seconds = int(t) // 60, t - (int(t) // 60) * 60
    if seconds == int(seconds):
        return f"{minutes:02d}:{int(seconds):02d}"
    return f"{minutes:02d}:{seconds:05.2f}"


class MarkingSession:
    """Acumula marcas de una sesión sobre las preexistentes del sidecar.

    Es la lógica del repaso (y de cualquier sesión de marcado), sin transporte:
    quien tenga los tiempos —mpv, el reloj de la sesión en vivo, un test— llama
    a :meth:`mark`/:meth:`undo` y al final escribe :meth:`sidecar_text`. Las
    preexistentes son intocables: deshacer solo retira lo de esta sesión.
    """

    def __init__(self, existing: list[float] | None = None):
        self._existing = sorted(existing or [])
        self._session: list[float] = []

    def mark(self, t: float) -> float | None:
        """Registra una marca en ``t``. None si se fundió con una cercana."""
        if any(abs(t - other) < MIN_MARK_GAP for other in self.all_marks()):
            return None
        self._session.append(t)
        return t

    def undo(self) -> float | None:
        """Retira la última marca de la sesión; None si no hay ninguna."""
        return self._session.pop() if self._session else None

    def all_marks(self) -> list[float]:
        """Unión ordenada de preexistentes y sesión."""
        return sorted(self._existing + self._session)

    def sidecar_text(self, name: str) -> str:
        """El contenido del sidecar: cabecera + un tiempo por línea.

        Mismo formato que la sesión en vivo — ASCII editable en cualquier
        editor y siempre parseable por :func:`file_marks`. La precisión se
        conserva (ver :func:`_timecode`).
        """
        lines = [f"# marcas de {name} - generadas por aurclips mark"]
        lines += [_timecode(t) for t in self.all_marks()]
        return "\n".join(lines) + "\n"


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
    session = MarkingSession()
    try:
        while True:
            input()
            t = session.mark(time.monotonic() - t0)
            if t is None:  # doble Enter nervioso: se funde, pero se dice
                print("  (a menos de 1 s de la anterior; no se duplica)")
            else:
                print(f"  marca {len(session.all_marks()):>2} en {_mmss(t)}")
    except (KeyboardInterrupt, EOFError):
        print()

    target.write_text(session.sidecar_text(name), encoding="utf-8")
    print(f"{len(session.all_marks())} marca(s) guardadas en {target}")
    print(f"Deja tu grabación como {cfg.inbox_dir / (name + '.mp4')} y corre 'run'.")
    return target


# ---------------------------------------------------------------------------
# Repaso (comando `mark` con la ruta de un video)
# ---------------------------------------------------------------------------

def _stdin_queue():
    """Cola alimentada por un hilo que lee líneas del terminal.

    Leer en un hilo permite que el loop principal también vigile si el
    reproductor sigue abierto: cerrar mpv termina la sesión aunque nadie
    vuelva a tocar el teclado.
    """
    import queue
    import sys
    import threading

    lines: queue.Queue[str] = queue.Queue()

    def reader():
        try:
            for line in sys.stdin:
                lines.put(line)
        except (ValueError, OSError):
            pass  # stdin cerrado: el loop principal sigue vigilando a mpv

    threading.Thread(target=reader, daemon=True).start()
    return lines


def review_session(video_path: str | Path) -> Path:
    """Repaso: ver la grabación en mpv y marcar con Enter el momento que suena.

    La marca cae donde está el cursor de reproducción — pausar también vale.
    Las marcas preexistentes del sidecar se conservan; al terminar (Ctrl+C o
    cerrar el reproductor) todo queda guardado junto al video.
    """
    import queue

    from .player import MpvPlayer, find_mpv

    video = Path(video_path)
    mpv = find_mpv()
    existing = file_marks(video)
    session = MarkingSession(existing)
    target = sidecar_path(video)

    print(f"Repasando: {video.name}")
    if existing:
        print(f"  ({len(existing)} marca(s) preexistentes; se conservan)")
    print("  Enter  -> marca el momento que está sonando — EN EL REPRODUCTOR")
    print("            (o en este terminal); pausar también vale")
    print("  u      -> deshacer la última marca (también en el reproductor)")
    print("  volumen en mpv: 9 baja · 0 sube · m mutea")
    print("  Ctrl+C o cerrar el reproductor -> terminar y guardar\n")

    player = MpvPlayer(mpv, video)
    lines = _stdin_queue()

    def do_mark():
        t = player.playback_time()
        if t is None:
            print("  no pude leer la posición (¿mpv sigue abriendo?)")
        elif session.mark(t) is None:
            print(f"  ya hay una marca en {_mmss(t)}; no se duplica")
            player.show_message(f"ya marcado ({_mmss(t)})")
        else:
            n = len(session.all_marks())
            print(f"  marca {n:>2} en {_mmss(t)}")
            player.show_message(f"marca {n} - {_mmss(t)}")

    def do_undo():
        undone = session.undo()
        if undone is None:
            print("  nada que deshacer en esta sesión")
            player.show_message("nada que deshacer")
        else:
            print(f"  deshecha la marca de {_mmss(undone)}")
            player.show_message(f"deshecha la marca de {_mmss(undone)}")

    try:
        while player.alive():
            # dos fuentes con las mismas teclas: el reproductor y el terminal
            event = player.poll_event(timeout=0.15)
            if event == "mark":
                do_mark()
            elif event == "undo":
                do_undo()
            try:
                command = lines.get_nowait().strip().lower()
            except queue.Empty:
                continue
            if command == "u":
                do_undo()
            elif command == "":
                do_mark()
            # cualquier otra entrada se ignora sin ruido
    except KeyboardInterrupt:
        print()
    finally:
        # guardar pase lo que pase: ni un error a mitad de repaso pierde
        # lo ya marcado
        player.close()
        target.write_text(session.sidecar_text(video.stem), encoding="utf-8")
        print(f"{len(session.all_marks())} marca(s) guardadas en {target}")
    return target
