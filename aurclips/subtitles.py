"""Subtítulos ASS estilo viral (Hormozi): MAYÚSCULAS, palabra por palabra,
una palabra clave resaltada por frase, posición en el tercio bajo.

Basado en lo que mejor retiene según la investigación:
- fuente condensada pesada, blanco con contorno negro grueso
- las palabras aparecen al ritmo del habla (snap, escala sutil <=105%)
- UNA palabra clave por frase en color (amarillo/verde), no toda la frase
- texto al ~70% de la altura (libre de la UI de Shorts/TikTok)
"""

from __future__ import annotations

from pathlib import Path

from .heuristics import HOOK_WORDS, STOPWORDS

PLAY_X, PLAY_Y = 1080, 1920

# Valores que se usan cuando la clave falta en config.yaml. config.yaml ship
# exactamente estos mismos números: si aquí y allí dicen cosas distintas, la
# documentación miente para quien borre una clave. Un test lo comprueba.
DEFAULT_FONT = "Anton"
DEFAULT_FONT_SIZE = 112
DEFAULT_OUTLINE = 10
DEFAULT_BASE_COLOR = "#FFFFFF"
DEFAULT_HIGHLIGHT_COLORS = ["#FFD93D", "#39FF14"]
DEFAULT_CAPTION_POSITION = 0.70
DEFAULT_WORDS_PER_CAPTION = 3

ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{size},{base},{base},&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,{outline},2,2,70,70,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _hex_to_ass(hex_color: str) -> str:
    """'#RRGGBB' -> formato ASS '&H00BBGGRR'."""
    c = hex_color.lstrip("#")
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H00{b}{g}{r}".upper()


def _display(word: str) -> str:
    """Palabra en MAYÚSCULAS, sin puntuación de cierre (se conservan ? y !)."""
    w = word.strip().upper()
    w = w.rstrip(".,;:").replace("{", "").replace("}", "")
    return w


def _group_words(words: list[dict], per_caption: int, max_gap: float = 0.9) -> list[list[dict]]:
    """Agrupa palabras en frases cortas, cortando en pausas."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    for w in words:
        if current:
            gap = w["start"] - current[-1]["end"]
            if len(current) >= per_caption or gap > max_gap:
                groups.append(current)
                current = []
        current.append(w)
    if current:
        groups.append(current)
    return groups


def _keyword_index(group: list[dict]) -> int | None:
    """Elige UNA palabra a resaltar: gancho conocido o la más 'pesada'."""
    best, best_len = None, 0
    for i, w in enumerate(group):
        token = w["word"].strip(".,;:!?").lower()
        if token in HOOK_WORDS:
            return i
        if len(token) >= 4 and token not in STOPWORDS and len(token) > best_len:
            best, best_len = i, len(token)
    return best


def build_ass(words: list[dict], cfg_render: dict, out_path: Path,
              font_name: str | None = None) -> Path:
    font = font_name or cfg_render.get("font", DEFAULT_FONT)
    size = cfg_render.get("font_size", DEFAULT_FONT_SIZE)
    outline = cfg_render.get("outline", DEFAULT_OUTLINE)
    base = _hex_to_ass(cfg_render.get("base_color", DEFAULT_BASE_COLOR))
    colors = [_hex_to_ass(c) for c in
              cfg_render.get("highlight_colors", DEFAULT_HIGHLIGHT_COLORS)] or [base]
    position = cfg_render.get("caption_position", DEFAULT_CAPTION_POSITION)
    margin_v = max(0, int(PLAY_Y * (1.0 - position)))
    per_caption = cfg_render.get("words_per_caption", DEFAULT_WORDS_PER_CAPTION)

    lines = [ASS_HEADER.format(font=font, size=size, base=base,
                               outline=outline, margin_v=margin_v)]

    groups = _group_words(words, per_caption)
    for g_idx, group in enumerate(groups):
        kw = _keyword_index(group)
        color = colors[g_idx % len(colors)]
        # cortesía de 0.15s tras la frase, pero ningún evento sobrevive a la
        # entrada de la frase siguiente: nunca dos captions en pantalla a la vez
        cutoff = (groups[g_idx + 1][0]["start"] if g_idx + 1 < len(groups)
                  else float("inf"))
        group_end = min(group[-1]["end"] + 0.15, cutoff)
        # una línea de diálogo por palabra hablada: muestra las palabras
        # acumuladas, la recién dicha entra con un "pop" sutil (105% -> 100%)
        for i, w in enumerate(group):
            start = w["start"]
            end = group[i + 1]["start"] if i + 1 < len(group) else group_end
            end = min(end, cutoff)
            if end - start < 0.03:
                continue
            parts = []
            for j in range(i + 1):
                token = _display(group[j]["word"])
                if not token:
                    continue
                tags = ""
                if j == i and i > 0:
                    tags += r"\fscx105\fscy105\t(0,70,\fscx100\fscy100)"
                if j == kw:
                    tags += rf"\c{color}&"
                if tags:
                    parts.append("{" + tags + "}" + token + r"{\r}")
                else:
                    parts.append(token)
            if not parts:
                continue
            lines.append(
                f"Dialogue: 0,{_ts(start)},{_ts(end)},Caption,,0,0,0,,"
                + " ".join(parts)
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return out_path
