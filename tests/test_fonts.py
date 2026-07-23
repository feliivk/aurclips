"""Tests de la resolución de fuentes para los subtítulos.

Seam bajo test: ``_resolve_font``. Lo que se afirma: la fuente Anton viaja con
el paquete (portable, sin descarga), así que se copia al workdir y se usa; y si
no hubiera ninguna fuente, el respaldo es una familia genérica que existe en
Linux/macOS, no la "Arial Black" de Windows.
"""

from pathlib import Path

from aurclips import render
from aurclips.render import _resolve_font, FALLBACK_FONT


class _Cfg:
    def get(self, key, default=None):
        return {"render.font": "Anton"}.get(key, default)


def test_anton_viaja_con_el_paquete():
    """El .ttf está comprometido en el repo, no depende del setup de Windows."""
    packaged = Path(render.__file__).resolve().parent / "assets" / "fonts" / "Anton-Regular.ttf"
    assert packaged.is_file()
    assert packaged.stat().st_size > 50_000  # una TTF real, no un placeholder


def test_la_fuente_empaquetada_se_copia_y_se_usa(tmp_path):
    name = _resolve_font(_Cfg(), tmp_path)
    assert name == "Anton"
    assert (tmp_path / "Anton-Regular.ttf").is_file()


def test_sin_ninguna_fuente_el_respaldo_es_generico(tmp_path, monkeypatch):
    """Con render.font=Anton y cero fuentes disponibles, no cae en Arial Black."""
    monkeypatch.setattr(render, "_font_source_dirs", lambda: (tmp_path / "vacio",))
    name = _resolve_font(_Cfg(), tmp_path)
    assert name == FALLBACK_FONT
    assert FALLBACK_FONT != "Arial Black"
