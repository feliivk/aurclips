"""Filtro de contenido no apto para monetización + detección de clips duplicados.

Dos verificaciones independientes, pensadas para correr sobre la transcripción
de cada clip antes de renderizarlo o subirlo (ver config: sección `safety` y
`dedup` en config.yaml):

- check_text():   busca palabras/frases que suelen desmonetizar o restringir
                   un video en YouTube (groserías fuertes, violencia gráfica,
                   sexual explícito, drogas duras, odio).
- find_duplicate(): compara el texto contra los textos ya aceptados para no
                   publicar el mismo contenido más de una vez. is_duplicate()
                   es el mismo criterio leyendo los clips de la base.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import NamedTuple

from .config import Config
from .state import State

# ---------------------------------------------------------------------------
# Lista integrada de términos problemáticos (es + en)
# ---------------------------------------------------------------------------
# Tamaño moderado pensado para un filtro de monetización razonable: cubre lo
# que YouTube suele desmonetizar o restringir (groserías fuertes, violencia
# gráfica, sexual explícito, drogas duras y odio). NO es un compendio
# exhaustivo de insultos o slurs — solo lo que un filtro básico necesita.
# Se combina en tiempo de ejecución con cfg.get("safety.extra_words", []).
UNSAFE_PATTERNS: tuple[str, ...] = (
    # --- violencia explícita / gráfica (no "matar"/"asesinar": son -------
    # --- demasiado genéricos y dependen del contexto) ---------------------
    "decapitar", "decapitación", "decapitado", "degollar", "degollado",
    "mutilar", "mutilación", "mutilado", "descuartizar", "descuartizado",
    "desmembrar", "behead", "beheading", "decapitate", "mutilate",
    "mutilation", "dismember",

    # --- sexual explícito --------------------------------------------------
    "porno", "pornografía", "pornográfico", "masturbación", "masturbarse",
    "eyaculación", "penetración", "sexo anal", "sexo oral", "orgía",
    "prostituta", "prostitución", "porn", "pornography", "masturbate",
    "masturbation", "blowjob", "handjob", "cumshot", "gangbang",

    # --- drogas duras --------------------------------------------------------
    "cocaína", "heroína", "fentanilo", "metanfetamina", "crack", "éxtasis",
    "mdma", "ketamina", "lsd", "cocaine", "heroin", "fentanyl",
    "methamphetamine", "crystal meth",

    # --- odio (conjunto representativo, no exhaustivo) ----------------------
    "nazi", "supremacía blanca", "white supremacy", "sudaca", "maricón",
    "retrasado mental", "faggot", "spic", "wetback", "chink", "nigger",
)

# Groserías comunes ("leves" para la política de YouTube desde 2023: la
# profanidad moderada es monetizable). Solo filtran con safety.strict: true —
# útil para canales de marca o infantiles; en gaming/streams es lenguaje normal.
MILD_PATTERNS: tuple[str, ...] = (
    # español (MX / ES / Rioplatense)
    "puta", "puto", "putas", "putos", "hijo de puta", "hijueputa",
    "hijoputa", "chinga tu madre", "chingada madre", "chingar", "chingada",
    "chingado", "concha de tu madre", "conchetumadre", "pendejo", "pendeja",
    "pendejada", "verga", "cabrón", "cabrona", "cabrones", "coño",
    "gilipollas", "mierda", "culero", "culera", "culiado", "culiada",
    "pelotudo", "pelotuda", "mamada", "mamadas", "malparido", "malparida",
    # inglés
    "fuck", "fucking", "fucked", "fucker", "motherfucker", "motherfucking",
    "shit", "shitty", "bullshit", "bitch", "bitches", "asshole", "cunt",
    "twat", "whore", "slut", "bastard", "douchebag", "dumbass", "cock",
    "dickhead",
)


def strip_mild(text: str) -> str:
    """Quita groserías leves de un texto. Para TÍTULOS y descripciones:
    YouTube sí limita anuncios por profanidad en título/miniatura aunque
    en el audio sea aceptable."""
    result = text
    for term in MILD_PATTERNS:
        result = re.sub(rf"(?i)\b{re.escape(term)}\b", "", result)
        # también la variante sin acentos del patrón ("cabron" vs "cabrón")
        plain = _strip_accents(term)
        if plain != term:
            result = re.sub(rf"(?i)\b{re.escape(plain)}\b", "", result)
    return re.sub(r"\s{2,}", " ", result).strip(" ,.;:¿¡")


def _strip_accents(text: str) -> str:
    """Quita diacríticos (á->a, é->e, ñ->n, ...) normalizando a NFD."""
    nfd = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")


def _normalize(text: str) -> str:
    """minúsculas + sin acentos + espacios colapsados, para comparar parejo."""
    lowered = _strip_accents(text.lower())
    return re.sub(r"\s+", " ", lowered).strip()


def check_text(cfg: Config, text: str) -> list[str]:
    """Palabras/frases problemáticas encontradas en el texto (lista vacía = apto).

    Combina la lista integrada UNSAFE_PATTERNS con cfg.get("safety.extra_words",
    []). La comparación ignora mayúsculas y acentos (normaliza ambos lados con
    unicodedata NFD) y usa límites de palabra (\\b) para no disparar sobre
    fragmentos de otras palabras. Devuelve los términos tal como están
    definidos en la lista (no el fragmento crudo del texto).
    """
    extra = cfg.get("safety.extra_words", []) or []
    patterns = (*UNSAFE_PATTERNS, *extra)
    # el modo estricto suma las groserías comunes (por defecto se permiten:
    # YouTube monetiza profanidad moderada y en gaming es lenguaje normal)
    if cfg.get("safety.strict", False):
        patterns = (*patterns, *MILD_PATTERNS)
    norm_text = _normalize(text)
    found: list[str] = []
    for term in patterns:
        norm_term = _normalize(str(term))
        if not norm_term:
            continue
        if re.search(rf"\b{re.escape(norm_term)}\b", norm_text):
            found.append(term)
    return found


# ---------------------------------------------------------------------------
# Deduplicación por similitud de Jaccard
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def _tokenize(text: str) -> set[str]:
    """Tokeniza para comparar: minúsculas, sin acentos, solo [a-z0-9]{3,}."""
    return set(_TOKEN_RE.findall(_normalize(text)))


def _jaccard(a: set[str], b: set[str]) -> float:
    """|A∩B| / |A∪B|; 0.0 si algún conjunto está vacío."""
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def find_duplicate(text: str, known: Iterable[tuple[int, str]],
                   threshold: float) -> tuple[bool, int | None]:
    """Compara un texto contra los textos ya aceptados. La política, sin origen.

    ``known`` son pares (id, texto): en el pipeline salen de la base y el id es
    el del clip; en el modo recortador salen de la propia corrida y el id es la
    posición del recorte suelto.

    Devuelve (True, id_del_parecido) si la similitud de Jaccard sobre conjuntos
    de tokens normalizados supera threshold; si no, (False, None). Los textos
    cortos (menos de 8 tokens) dan más falsos positivos, así que para ellos se
    exige threshold + 0.1.
    """
    tokens = _tokenize(text)
    if not tokens:
        return False, None
    effective_threshold = threshold + 0.1 if len(tokens) < 8 else threshold

    for known_id, known_text in known:
        similarity = _jaccard(tokens, _tokenize(known_text))
        if similarity > effective_threshold:
            return True, known_id
    return False, None


def is_duplicate(db: State, text: str, threshold: float) -> tuple[bool, int | None]:
    """Como find_duplicate, contra la columna clips.text de la base."""
    return find_duplicate(
        text,
        ((row["id"], row["text"]) for row in db.texts_for_dedup()),
        threshold,
    )


# ---------------------------------------------------------------------------
# Las dos verificaciones juntas: qué se hace con un clip
# ---------------------------------------------------------------------------

class Verdict(NamedTuple):
    """Qué hacer con un clip, y por qué.

    ``keep`` es si sobrevive; ``flagged`` es si queda señalado (el filtro lo
    apartó por lo que dice, pero decides tú). Los otros dos campos son los
    hechos con que cada modo redacta su propio mensaje.
    """
    keep: bool
    flagged: bool
    unsafe_terms: list[str]
    duplicate_of: int | None


def screen_clip(cfg: Config, text: str,
                known: Iterable[tuple[int, str]]) -> Verdict:
    """Filtro de contenido y limpieza de duplicados sobre el texto de un clip.

    La política vive aquí una sola vez: la usan el pipeline (comparando contra
    los clips de la base) y el modo recortador (contra los recortes de la
    propia corrida). Cambiarla aquí la cambia en los dos.
    """
    flagged = False
    unsafe: list[str] = []
    if cfg.get("safety.enabled", True):
        unsafe = check_text(cfg, text)
        if unsafe:
            if cfg.get("safety.action", "skip") == "skip":
                return Verdict(False, False, unsafe, None)
            # señalado: sigue compitiendo, pero marcado para que lo mires
            flagged = True
    if cfg.get("dedup.enabled", True):
        duplicate, other = find_duplicate(
            text, known, cfg.get("dedup.similarity", 0.8))
        if duplicate:
            return Verdict(False, flagged, unsafe, other)
    return Verdict(True, flagged, unsafe, None)
