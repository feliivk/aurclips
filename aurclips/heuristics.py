"""Selección local de highlights: energía de audio + ritmo + ganchos del texto.

No usa ninguna API. Analiza el audio con ffmpeg y la transcripción de Whisper
para puntuar ventanas candidatas y generar título/descripción/hashtags.

El motor es deliberadamente simple y honesto: no modela arcos narrativos ni
persigue la viralidad, porque eso no se alcanza a punta de heurística. La
narrativa se resuelve arriba (grabando en beats y marcándolos, ver
``marks.py``) y la calidad se mide abajo (``stats.py``). Aquí solo se afina
*a tu género* con los pesos de :class:`Weights`.
"""

from __future__ import annotations

import math
import re
import subprocess
from array import array
from collections import Counter
from dataclasses import dataclass, field, replace

from .config import Config
from .marks import Marks

ENERGY_WINDOW = 0.5  # segundos por ventana de energía

SENTENCE_END = (".", "?", "!")  # un texto que termina así cierra la idea
MIN_GAP_S = 30.0  # separación mínima entre clips elegidos del mismo video


def _ends_sentence(text: str) -> bool:
    return text.rstrip().endswith(SENTENCE_END)


def _significant_words(text: str) -> list[str]:
    """Palabras con contenido: largas y fuera de las stopwords."""
    return [w for w in re.findall(r"[a-záéíóúüñ]{4,}", text.lower())
            if w not in STOPWORDS]

# Palabras que suelen abrir un buen gancho (es + en)
HOOK_WORDS = {
    "secreto", "nunca", "nadie", "error", "truco", "gratis", "dinero", "peor",
    "mejor", "increible", "increíble", "importante", "cuidado", "verdad",
    "mentira", "mira", "escucha", "atencion", "atención", "locura", "brutal",
    "secret", "never", "nobody", "mistake", "trick", "free", "money", "worst",
    "best", "insane", "important", "careful", "truth", "crazy", "listen",
}

# Arranques de relleno: se limpian del título y penalizan el inicio de un clip
FILLER_STARTS = (
    "y ", "pero ", "bueno ", "entonces ", "o sea ", "osea ", "este ", "eh ",
    "pues ", "a ver ", "vale ", "ok ", "and ", "but ", "so ", "well ", "um ",
    "uh ", "like ",
)

# Genéricos que no aportan como hashtag aunque sean frecuentes
GENERIC_TAGS = {
    "video", "videos", "clip", "clips", "short", "shorts", "canal", "gente",
    "cosas", "cosa", "tema", "temas", "parte", "momento", "momentos", "hoy",
    "manera", "forma", "vida", "mundo", "channel", "stuff", "thing",
}

STOPWORDS = {
    # español
    "que", "de", "la", "el", "en", "y", "a", "los", "las", "del", "se", "un",
    "una", "por", "con", "no", "es", "lo", "como", "para", "mas", "más", "pero",
    "sus", "le", "ya", "o", "este", "si", "sí", "porque", "esta", "entre",
    "cuando", "muy", "sin", "sobre", "también", "me", "hasta", "hay", "donde",
    "quien", "desde", "todo", "nos", "durante", "todos", "uno", "les", "ni",
    "contra", "otros", "ese", "eso", "ante", "ellos", "e", "esto", "mí", "antes",
    "algunos", "qué", "unos", "yo", "otro", "otras", "otra", "él", "tanto",
    "esa", "estos", "mucho", "quienes", "nada", "muchos", "cual", "poco",
    "ella", "estar", "estas", "algunas", "algo", "nosotros", "tiene", "tienen",
    "era", "eres", "soy", "somos", "está", "están", "fue", "ser", "hacer",
    "hace", "puede", "pueden", "tengo", "vamos", "bueno", "entonces", "pues",
    "osea", "creo", "digo", "dice", "decir", "ahora", "aquí", "ahí", "así",
    # inglés
    "the", "be", "to", "of", "and", "in", "that", "have", "it", "for", "not",
    "on", "with", "he", "as", "you", "do", "at", "this", "but", "his", "by",
    "from", "they", "we", "say", "her", "she", "or", "an", "will", "my", "one",
    "all", "would", "there", "their", "what", "so", "up", "out", "if", "about",
    "who", "get", "which", "go", "me", "when", "make", "can", "like", "time",
    "just", "him", "know", "take", "people", "into", "year", "your", "good",
    "some", "could", "them", "see", "other", "than", "then", "now", "look",
    "only", "come", "its", "over", "think", "also", "back", "after", "use",
    "two", "how", "our", "work", "first", "well", "way", "even", "new", "want",
    "because", "any", "these", "give", "day", "most", "us", "was", "were",
    "been", "being", "are", "is", "very", "really", "going", "gonna", "yeah",
    "without", "here", "there", "every", "single", "thing", "things",
    "something", "everything", "anything", "nothing", "actually", "always",
    "never", "still", "much", "many", "where", "while", "again", "right",
    "okay", "need", "keep", "let", "lets", "got", "does", "did", "doing",
    "means", "part", "whole", "follows", "already",
}


@dataclass
class Candidate:
    start: float
    end: float
    score: float
    segments: list = field(default_factory=list)
    marked: bool = False  # cae sobre una marca tuya (ver marks.py)

    @property
    def text(self) -> str:
        return " ".join(s["text"] for s in self.segments)

    @property
    def duration(self) -> float:
        return self.end - self.start


def _far_enough(a: Candidate, b: Candidate) -> bool:
    """¿Dos candidatos pueden convivir? Los marcados solo piden no traslaparse."""
    gap = 0.0 if (a.marked and b.marked) else MIN_GAP_S
    return a.end + gap <= b.start or a.start >= b.end + gap


# ---------------------------------------------------------------------------
# Energía de audio
# ---------------------------------------------------------------------------

def audio_energy(cfg: Config, video_path: str) -> list[float]:
    """RMS del audio por ventana de ENERGY_WINDOW segundos (vía ffmpeg)."""
    try:
        cmd = [
            cfg.ffmpeg, "-v", "error", "-i", video_path,
            "-map", "0:a:0", "-f", "s16le", "-ac", "1", "-ar", "8000", "-",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    except OSError:  # ffmpeg ausente (setup incompleto): energía neutra
        return []
    chunk_bytes = int(8000 * ENERGY_WINDOW) * 2
    energies: list[float] = []
    try:
        import audioop  # rápido (C); existe en Python <= 3.12

        def rms(chunk: bytes) -> float:
            return float(audioop.rms(chunk, 2))
    except ImportError:
        def rms(chunk: bytes) -> float:
            samples = array("h", chunk[: len(chunk) // 2 * 2])
            if not samples:
                return 0.0
            return math.sqrt(sum(s * s for s in samples) / len(samples))

    while True:
        chunk = proc.stdout.read(chunk_bytes)
        if not chunk:
            break
        energies.append(rms(chunk))
    proc.wait()
    return energies


def _percentile_ranks(values: list[float]) -> list[float]:
    """Convierte valores a rangos 0..1 (robusto frente a volumen absoluto)."""
    if not values:
        return []
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    denom = max(1, len(values) - 1)
    for rank, idx in enumerate(order):
        ranks[idx] = rank / denom
    return ranks


# ---------------------------------------------------------------------------
# Pesos: el mismo motor, afinado a cómo suenas tú
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Weights:
    """Cuánto puede aportar (o restar) cada señal a la puntuación final.

    Cada campo es el **máximo** que esa señal mueve la aguja, así que se leen
    y se comparan directamente: si ``closes`` > ``energy``, cerrar la idea
    importa más que el volumen.
    """

    energy: float    # picos de audio (rango percentil de la ventana)
    pace: float      # ritmo de habla contra la mediana del video
    hook: float      # palabras gancho en los primeros 8 s
    punct: float     # preguntas y exclamaciones
    closes: float    # el clip termina cerrando la idea
    density: float   # fracción de palabras con contenido (no muletillas)
    filler: float    # penalización: arranca con muletilla
    gaps: float      # penalización: silencios muertos dentro de la ventana
    mark: float      # tú marcaste ese momento al grabar (ver marks.py)

    @classmethod
    def from_config(cls, cfg: Config) -> "Weights":
        name = cfg.get("selection.profile", "comentario")
        base = PROFILES.get(name)
        if base is None:
            print(f"  [selector] perfil '{name}' desconocido; se usa 'comentario'")
            base = PROFILES["comentario"]
        overrides = cfg.get("selection.weights") or {}
        known = {k: float(v) for k, v in overrides.items()
                 if k in cls.__dataclass_fields__}
        unknown = set(overrides) - set(cls.__dataclass_fields__)
        if unknown:  # un typo en la config no debe fallar en silencio
            print(f"  [selector] pesos desconocidos, ignorados: "
                  f"{', '.join(sorted(unknown))}")
        return replace(base, **known) if known else base


PROFILES: dict[str, Weights] = {
    # Charla tranquila (comentario, análisis, podcast, tutorial): no gritas,
    # así que el volumen dice poco de dónde está lo bueno. Manda que la idea
    # cierre y que haya contenido real, no relleno conversacional.
    "comentario": Weights(energy=0.12, pace=0.15, hook=0.35, punct=0.15,
                          closes=0.28, density=0.22, filler=0.15, gaps=0.40,
                          mark=0.50),
    # Gaming, reacciones, streams: ahí los picos de audio sí señalan el
    # momento (risas, gritos, subidas de intensidad).
    "gaming": Weights(energy=0.30, pace=0.20, hook=0.30, punct=0.15,
                      closes=0.18, density=0.12, filler=0.12, gaps=0.40,
                      mark=0.50),
}


# ---------------------------------------------------------------------------
# Puntuación de ventanas candidatas
# ---------------------------------------------------------------------------

def _window_energy(ranks: list[float], start: float, end: float) -> float:
    if not ranks:
        return 0.5
    a = min(len(ranks) - 1, int(start / ENERGY_WINDOW))
    b = min(len(ranks), max(a + 1, int(end / ENERGY_WINDOW)))
    window = ranks[a:b]
    return sum(window) / len(window)


def _score_window(segs: list[dict], energy_ranks: list[float],
                  median_pace: float, w: Weights, marked: bool = False) -> float:
    start, end = segs[0]["start"], segs[-1]["end"]
    dur = max(0.1, end - start)
    text = " ".join(s["text"] for s in segs).lower()

    energy = w.energy * _window_energy(energy_ranks, start, end)

    n_words = sum(len(s["words"]) for s in segs)
    pace = w.pace * min(2.0, (n_words / dur) / max(0.1, median_pace)) / 2.0

    first_8s = " ".join(s["text"] for s in segs if s["start"] < start + 8).lower()
    hook_hits = sum(1 for word in HOOK_WORDS if word in first_8s)
    hook = min(w.hook, hook_hits * w.hook / 2)

    punct = min(w.punct, (text.count("?") + text.count("!")) * w.punct / 3)

    gaps = 0.0
    for prev, nxt in zip(segs, segs[1:]):
        gaps += max(0.0, nxt["start"] - prev["end"] - 1.0)
    gap_penalty = min(w.gaps, gaps / dur)

    # cerrar la idea: un clip que muere a mitad de frase se siente roto
    # aunque el momento sea bueno
    closes_sentence = w.closes if _ends_sentence(text) else 0.0

    # arrancar con muletilla desperdicia el primer segundo del Short
    filler_pen = w.filler if text.lstrip().startswith(FILLER_STARTS) else 0.0

    # densidad de contenido: fracción de palabras significativas (no
    # stopwords); el relleno conversacional puro no merece un Short
    tokens = re.findall(r"[a-záéíóúüñ]+", text)
    density = (w.density * min(1.0, 2 * len(_significant_words(text)) / len(tokens))
               if tokens else 0.0)

    # duración óptima según la investigación: 15-30s retiene mejor; la
    # retención cae fuerte pasados los ~45s
    if dur <= 35:
        dur_bonus = 0.08
    elif dur <= 45:
        dur_bonus = 0.03
    else:
        dur_bonus = -0.05

    # tu marca gana a cualquier heurística: si señalaste el momento al
    # grabar, sabes algo que el audio y el texto no dicen
    mark_bonus = w.mark if marked else 0.0

    return (energy + pace + hook + punct + closes_sentence + dur_bonus
            + density + mark_bonus - gap_penalty - filler_pen)


def find_candidates(cfg: Config, transcript: dict, video_path: str,
                    limit: int, marks: Marks | None = None) -> list[Candidate]:
    """Devuelve las mejores ventanas candidatas, sin traslapes."""
    min_s = cfg.get("selection.min_clip_seconds", 15)
    max_s = cfg.get("selection.max_clip_seconds", 59)
    marks = marks or Marks()
    tolerance = cfg.get("marks.tolerance", 3.0)
    segs = [s for s in transcript["segments"]
            if s["words"] and float(s["start"]) not in marks.muted_starts]
    if not segs:
        return []

    weights = Weights.from_config(cfg)
    print("  [selector] analizando energía del audio...")
    energy_ranks = _percentile_ranks(audio_energy(cfg, video_path))

    paces = [len(s["words"]) / max(0.1, s["end"] - s["start"]) for s in segs]
    median_pace = sorted(paces)[len(paces) // 2]

    # todas las ventanas que empiezan en un límite de frase
    windows: list[Candidate] = []
    for i in range(len(segs)):
        best: Candidate | None = None
        j = i
        while j < len(segs) and segs[j]["end"] - segs[i]["start"] <= max_s + 2:
            dur = segs[j]["end"] - segs[i]["start"]
            if dur >= min_s:
                window = segs[i:j + 1]
                marked = marks.covers(segs[i]["start"], segs[j]["end"], tolerance)
                score = _score_window(window, energy_ranks, median_pace,
                                      weights, marked)
                if best is None or score > best.score:
                    best = Candidate(segs[i]["start"], segs[j]["end"], score,
                                     window, marked=marked)
            j += 1
        if best:
            windows.append(best)

    # si marcaste el video, esas ventanas son el material: el resto sobra
    if marks and cfg.get("marks.exclusive", True):
        only_marked = [c for c in windows if c.marked]
        if only_marked:
            print(f"  [marcas] {len(only_marked)} ventana(s) sobre tus marcas; "
                  f"se ignora el resto del video (marks.exclusive)")
            windows = only_marked

    # selección voraz sin traslapes y con separación mínima; entre dos clips
    # marcados basta con que no se traslapen (marcaste los dos a propósito)
    windows.sort(key=lambda c: c.score, reverse=True)
    chosen: list[Candidate] = []
    for cand in windows:
        if len(chosen) >= limit:
            break
        if all(_far_enough(cand, c) for c in chosen):
            chosen.append(cand)
    chosen.sort(key=lambda c: c.start)

    # recorte a frase completa: si la ventana ganadora no termina cerrando
    # la idea, retroceder hasta el último segmento que sí lo haga, sin bajar
    # de la duración mínima; si no hay corte válido, se conserva el final
    for cand in chosen:
        if _ends_sentence(cand.segments[-1]["text"]):
            continue
        for j in range(len(cand.segments) - 2, -1, -1):
            seg = cand.segments[j]
            if seg["end"] - cand.start < min_s:
                break
            if _ends_sentence(seg["text"]):
                cand.segments = cand.segments[:j + 1]
                cand.end = seg["end"]
                break
    return chosen


# ---------------------------------------------------------------------------
# Metadatos sin LLM (respaldo: con Ollama los escribe titles.py)
# ---------------------------------------------------------------------------

def _clean_title(text: str, max_len: int = 85) -> str:
    text = text.strip()
    lowered = text.lower()
    for filler in FILLER_STARTS:
        if lowered.startswith(filler):
            text = text[len(filler):].strip()
            lowered = text.lower()
    text = text.rstrip(".,;: ")
    if len(text) > max_len:
        cut = text[:max_len].rsplit(" ", 1)[0]
        text = cut.rstrip(".,;: ")
    return text[:1].upper() + text[1:] if text else "Clip"


def split_sentences(text: str) -> list[str]:
    """Frases del clip, sin los fragmentos vacíos."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _sentence_appeal(sentence: str) -> tuple[int, int]:
    """Prioridad de una frase como título: (gancho, contenido)."""
    lowered = sentence.lower()
    if any(w in lowered for w in HOOK_WORDS):
        rank = 3
    elif "?" in sentence or "¿" in sentence or "!" in sentence or "¡" in sentence:
        rank = 2
    elif len(_significant_words(sentence)) >= 3:
        rank = 1
    else:
        rank = 0
    return rank, len(_significant_words(sentence))


def best_sentence(text: str, used: set[str] | None = None) -> str:
    """La frase con más gancho del clip — no la primera por ser la primera.

    La primera frase casi nunca es la que engancha: suele ser el arranque de
    la idea, no su filo. Se prefiere la que trae palabra gancho, luego la
    pregunta/exclamación, y solo al final se cae en el orden original.
    """
    sentences = split_sentences(text)
    if not sentences:
        return ""
    used = used or set()
    fresh = [s for s in sentences
             if _clean_title(s).lower() not in used] or sentences
    return max(fresh, key=lambda s: (_sentence_appeal(s), -fresh.index(s)))


def _description(text: str, max_len: int = 220) -> str:
    """Primeras frases completas del clip (nunca un corte a media palabra)."""
    out = ""
    for sentence in split_sentences(text):
        if out and len(out) + 1 + len(sentence) > max_len:
            break
        out = f"{out} {sentence}".strip()
        if len(out) >= max_len * 0.6:
            break
    if out:
        return out
    text = text.strip()
    return text[:max_len].rsplit(" ", 1)[0] + "…" if len(text) > max_len else text


def _hashtags(text: str, limit: int = 4) -> list[str]:
    freq = Counter(w for w in _significant_words(text) if w not in GENERIC_TAGS)
    return [w for w, _ in freq.most_common(limit)]


def make_metadata(cand: Candidate,
                  used_titles: set[str] | None = None) -> tuple[str, str, list[str]]:
    """(título, descripción, hashtags) generados a partir del propio clip.

    ``used_titles`` (en minúsculas) evita que dos clips del mismo video salgan
    con el mismo título.
    """
    from .safety import strip_mild  # título sin groserías (política de títulos)

    text = cand.text.strip()
    title = _clean_title(strip_mild(best_sentence(text, used_titles)))
    return title, _description(text), _hashtags(text)
