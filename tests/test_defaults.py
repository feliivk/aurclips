"""Los defaults del código y los de config.yaml dicen lo mismo.

Cada clave de render vive en dos sitios: el `config.yaml` que ship el repo y el
respaldo del código para cuando la clave falta. Es una duplicación que no se
puede quitar —config.yaml es documentación ejecutable y el respaldo es una red
de seguridad— pero sí se puede impedir que se separen, que es lo que pasa en la
práctica: alguien afina un valor en la config y el respaldo se queda diciendo
otra cosa, así que la documentación miente para quien borre la clave.

Este test es esa red. Si falla, cambia el que se te olvidó.
"""

from pathlib import Path

import yaml

from aurclips import render, subtitles
from aurclips.config import ROOT

# (clave con puntos en config.yaml, constante del código)
DEFAULTS = [
    ("render.subtitles", render.DEFAULT_SUBTITLES),
    ("render.words_per_caption", subtitles.DEFAULT_WORDS_PER_CAPTION),
    ("render.font", subtitles.DEFAULT_FONT),
    ("render.font_size", subtitles.DEFAULT_FONT_SIZE),
    ("render.outline", subtitles.DEFAULT_OUTLINE),
    ("render.base_color", subtitles.DEFAULT_BASE_COLOR),
    ("render.highlight_colors", subtitles.DEFAULT_HIGHLIGHT_COLORS),
    ("render.caption_position", subtitles.DEFAULT_CAPTION_POSITION),
    ("render.tighten_silences", render.DEFAULT_TIGHTEN_SILENCES),
    ("render.max_pause", render.DEFAULT_MAX_PAUSE),
    ("render.crf", render.DEFAULT_CRF),
    ("render.preset", render.DEFAULT_PRESET),
    ("crop.face_tracking", render.DEFAULT_FACE_TRACKING),
]


def _shipped() -> dict:
    return yaml.safe_load(Path(ROOT / "config.yaml").read_text(encoding="utf-8"))


def _at(doc: dict, dotted: str):
    node = doc
    for part in dotted.split("."):
        node = node[part]
    return node


def test_config_yaml_ship_los_mismos_defaults_que_el_codigo():
    doc = _shipped()
    desajustados = [
        f"{key}: config.yaml dice {_at(doc, key)!r} y el código {value!r}"
        for key, value in DEFAULTS
        if _at(doc, key) != value
    ]
    assert not desajustados, "\n".join(desajustados)


def test_todas_las_claves_comprobadas_existen_en_config_yaml():
    """Si una clave se renombra, este test lo dice en vez de dejar de mirar."""
    doc = _shipped()
    for key, _ in DEFAULTS:
        _at(doc, key)  # KeyError si desapareció
