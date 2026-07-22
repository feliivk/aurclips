"""Tests de las marcas del creador.

Seam bajo test: el mismo ``select_clips`` (config + transcripción + ruta de
video -> lista de Clips). Lo que se afirma es la promesa de la palanca de
arriba: si tú marcaste el momento, ese es el clip, aunque la heurística
prefiera otro trozo del video.
"""

from pathlib import Path

import yaml

from aurclips.config import Config
from aurclips.marks import parse_timecode, sidecar_path
from aurclips.select_clips import select_clips


def _cfg(tmp_path: Path, marks: dict | None = None, **selection) -> Config:
    """Config mínima en tmp: heurística pura, sin LLM y con marcas activas."""
    sel = {"min_clip_seconds": 15, "max_clip_seconds": 59}
    sel.update(selection)
    doc = {
        "selection": sel,
        "titles": {"engine": "heuristic"},
        "marks": {"enabled": True, "phrases": ["esto es un short"],
                  "exclusive": True, "tolerance": 3.0},
    }
    doc["marks"].update(marks or {})
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return Config(path)


def _seg(start: float, end: float, text: str) -> dict:
    tokens = text.split()
    dur = (end - start) / len(tokens)
    return {
        "start": start, "end": end, "text": text,
        "words": [{"word": w, "start": start + i * dur,
                   "end": start + (i + 1) * dur}
                  for i, w in enumerate(tokens)],
    }


FLAT = "palabras normales que rellenan la charla continua sin nada especial"
# zona que la heurística adora: ganchos, preguntas y cierre de idea
HOT_A = "cuidado este secreto es increible ¿nadie lo sabia? ¡brutal de verdad!"
HOT_B = "mira esta verdad importante que nadie cuenta nunca. es la mejor."


def _video(tmp_path: Path) -> str:
    """Ruta de video que no existe: ffprobe degrada y la energía es neutra."""
    return str(tmp_path / "grabacion.mp4")


def _transcript(total_s: float = 240.0, seg_s: float = 10.0) -> dict:
    segs = [_seg(t, min(t + seg_s, total_s), FLAT)
            for t in range(0, int(total_s), int(seg_s))]
    return {"segments": segs}


def _con_gancho_y_marca() -> dict:
    """Gancho fuerte en el minuto 1; marca hablada justo antes del minuto 3."""
    tr = _transcript()
    tr["segments"][6] = _seg(60.0, 70.0, HOT_A)
    tr["segments"][7] = _seg(70.0, 80.0, HOT_B)
    tr["segments"][17] = _seg(170.0, 180.0, "Esto es un short.")
    return tr


# --- marcas por voz -----------------------------------------------------

def test_la_marca_hablada_gana_al_mejor_momento_heuristico(tmp_path):
    # el gancho del minuto 1 puntúa mucho más alto, pero tú marcaste el
    # minuto 3: manda tu marca
    cfg = _cfg(tmp_path, clips_per_video=1)
    clips = select_clips(cfg, _con_gancho_y_marca(), "con marca", _video(tmp_path))
    assert len(clips) == 1
    assert clips[0].start_s - 3 <= 180 <= clips[0].end_s + 3
    assert clips[0].marked


def test_la_frase_gatillo_no_entra_en_el_clip(tmp_path):
    # la marca señala el momento pero no forma parte de él: el segmento que
    # solo dice la frase se silencia
    cfg = _cfg(tmp_path, clips_per_video=1)
    clips = select_clips(cfg, _con_gancho_y_marca(), "con marca", _video(tmp_path))
    assert "short" not in clips[0].text.lower()


def test_sin_exclusividad_la_heuristica_vuelve_a_competir(tmp_path):
    # con marks.exclusive: false el resto del video sigue en juego, así que
    # el gancho fuerte del minuto 1 también sale
    cfg = _cfg(tmp_path, marks={"exclusive": False}, clips_per_video=2,
               minutes_per_short=0)
    clips = select_clips(cfg, _con_gancho_y_marca(), "sin exclusividad",
                         _video(tmp_path))
    assert len(clips) == 2
    assert any(c.start_s < 100 for c in clips)
    assert any(c.marked for c in clips)


def test_sin_marcas_todo_sigue_igual(tmp_path):
    # el video sin marcas se comporta como siempre: gana el gancho
    cfg = _cfg(tmp_path, clips_per_video=1)
    tr = _transcript()
    tr["segments"][6] = _seg(60.0, 70.0, HOT_A)
    tr["segments"][7] = _seg(70.0, 80.0, HOT_B)
    clips = select_clips(cfg, tr, "sin marcas", _video(tmp_path))
    assert len(clips) == 1
    assert clips[0].start_s < 100
    assert not clips[0].marked


# --- marcas por archivo -------------------------------------------------

def test_el_sidecar_marca_el_momento(tmp_path):
    # un <video>.marks.txt junto a la grabación (hotkey / `aurclips mark`)
    video = _video(tmp_path)
    sidecar_path(video).write_text("# grabación de prueba\n03:00\n",
                                   encoding="utf-8")
    cfg = _cfg(tmp_path, clips_per_video=1)
    tr = _transcript()
    tr["segments"][6] = _seg(60.0, 70.0, HOT_A)  # el gancho compite y pierde
    tr["segments"][7] = _seg(70.0, 80.0, HOT_B)
    clips = select_clips(cfg, tr, "marca de archivo", video)
    assert len(clips) == 1
    assert clips[0].start_s - 3 <= 180 <= clips[0].end_s + 3


def test_marcas_desactivadas_se_ignoran(tmp_path):
    video = _video(tmp_path)
    sidecar_path(video).write_text("03:00\n", encoding="utf-8")
    cfg = _cfg(tmp_path, marks={"enabled": False}, clips_per_video=1)
    tr = _transcript()
    tr["segments"][6] = _seg(60.0, 70.0, HOT_A)
    tr["segments"][7] = _seg(70.0, 80.0, HOT_B)
    clips = select_clips(cfg, tr, "marcas apagadas", video)
    assert clips[0].start_s < 100


def test_formatos_de_tiempo_del_sidecar():
    assert parse_timecode("754") == 754
    assert parse_timecode("12:34") == 754
    assert parse_timecode("1:02:03") == 3723
    assert parse_timecode("00:30  # buen remate") == 30
    assert parse_timecode("") is None
    assert parse_timecode("no es un tiempo") is None
