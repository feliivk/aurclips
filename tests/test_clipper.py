"""Tests del modo recortador.

Seam bajo test: ``plan_clips`` (config + transcripción -> los recortes que se
van a renderizar) y la metadata que acompaña a cada uno. Es todo lo que el
modo recortador decide; transcribir y quemar con ffmpeg quedan fuera a
propósito.

Sin video real, igual que en los tests del selector: ffprobe degrada a la
duración de la transcripción y la energía de audio a neutra.
"""

from pathlib import Path
from types import SimpleNamespace

import yaml

from aurclips.clipper import metadata_text, plan_clips, write_metadata
from aurclips.config import Config

NO_VIDEO = "no_existe.mp4"

FLAT = "palabras normales que rellenan la charla continua sin nada especial"
DROGAS = "el vecino guardaba cocaina debajo del sofá y nadie lo sabía nunca"


def _cfg(tmp_path: Path, safety: dict | None = None, dedup: dict | None = None,
         **selection) -> Config:
    sel = {"min_clip_seconds": 15, "max_clip_seconds": 59,
           "minutes_per_short": 1}
    sel.update(selection)
    doc = {"selection": sel,
           "titles": {"engine": "heuristic", "url": "http://127.0.0.1:9"},
           "safety": safety if safety is not None else {"enabled": False},
           "dedup": dedup if dedup is not None else {"enabled": False}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return Config(path)


def _seg(start: float, end: float, text: str) -> dict:
    tokens = text.split()
    dur = (end - start) / len(tokens)
    return {"start": start, "end": end, "text": text,
            "words": [{"word": w, "start": start + i * dur,
                       "end": start + (i + 1) * dur}
                      for i, w in enumerate(tokens)]}


def _transcript(total_s: float, text: str = FLAT, seg_s: float = 10.0) -> dict:
    return {"segments": [_seg(t, min(t + seg_s, total_s), text)
                         for t in range(0, int(total_s), int(seg_s))]}


def _corto(text: str) -> dict:
    """Una grabación que ya cabe como Short: el selector la usa entera."""
    return {"segments": [_seg(0, 30, text), _seg(30, 60, text)]}


# --- el filtro de contenido -------------------------------------------------

def test_un_recorte_con_termino_no_apto_se_descarta(tmp_path):
    cfg = _cfg(tmp_path, safety={"enabled": True, "action": "skip"})
    assert plan_clips(cfg, _corto(DROGAS), "partida", NO_VIDEO) == []


def test_con_flag_el_recorte_se_conserva_para_que_tu_decidas(tmp_path):
    cfg = _cfg(tmp_path, safety={"enabled": True, "action": "flag"})
    assert len(plan_clips(cfg, _corto(DROGAS), "partida", NO_VIDEO)) == 1


def test_sin_filtro_el_texto_no_se_mira(tmp_path):
    cfg = _cfg(tmp_path, safety={"enabled": False})
    assert len(plan_clips(cfg, _corto(DROGAS), "partida", NO_VIDEO)) == 1


def test_un_recorte_limpio_pasa_el_filtro(tmp_path):
    cfg = _cfg(tmp_path, safety={"enabled": True, "action": "skip"})
    assert len(plan_clips(cfg, _corto(FLAT), "partida", NO_VIDEO)) == 1


# --- duplicados dentro de la misma corrida ---------------------------------

def test_sin_dedup_salen_todas_las_ventanas(tmp_path):
    """Referencia: charla pareja, tres ventanas pedidas, tres recortes."""
    cfg = _cfg(tmp_path, clips_per_video=3)
    assert len(plan_clips(cfg, _transcript(480), "charla", NO_VIDEO)) == 3


def test_las_ventanas_casi_identicas_dejan_un_solo_recorte(tmp_path):
    """La misma charla repetida no son tres Shorts: es uno, tres veces."""
    cfg = _cfg(tmp_path, dedup={"enabled": True, "similarity": 0.8},
               clips_per_video=3)
    assert len(plan_clips(cfg, _transcript(480), "charla", NO_VIDEO)) == 1


# --- el tope de recortes ----------------------------------------------------

def test_el_tope_de_recortes_se_respeta(tmp_path):
    cfg = _cfg(tmp_path, clips_per_video=2)
    assert len(plan_clips(cfg, _transcript(480), "charla", NO_VIDEO)) == 2


def test_el_tope_se_puede_pisar_solo_para_esta_corrida(tmp_path):
    """Es lo que hace `clip --clips N`: pisa la config sin escribirla."""
    cfg = _cfg(tmp_path, clips_per_video=3)
    cfg.override("selection.clips_per_video", 1)
    assert len(plan_clips(cfg, _transcript(480), "charla", NO_VIDEO)) == 1
    assert cfg.get("selection.min_clip_seconds") == 15  # el resto, intacto


def test_pisar_una_clave_que_no_existe_la_crea(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.override("render.font_size", 160)
    assert cfg.get("render.font_size") == 160


# --- la metadata que acompaña al recorte ------------------------------------

def _clip(title="Un título con gancho", description="Una descripción",
          tags=("gaming", "shorts")) -> SimpleNamespace:
    return SimpleNamespace(title=title, description=description,
                           tags=list(tags), marked=False)


def test_la_metadata_trae_titulo_descripcion_y_hashtags():
    texto = metadata_text(_clip())
    assert "Un título con gancho" in texto
    assert "Una descripción" in texto
    assert "#gaming #shorts" in texto


def test_los_hashtags_no_se_duplican_la_almohadilla():
    assert "##" not in metadata_text(_clip(tags=("#gaming", "shorts")))


def test_sin_descripcion_la_metadata_sigue_saliendo():
    """Sin Ollama la heurística puede no dar descripción; el archivo no queda roto."""
    texto = metadata_text(_clip(description="", tags=("gaming",)))
    assert texto.startswith("Un título con gancho")
    assert "#gaming" in texto
    assert "\n\n\n" not in texto


def test_el_archivo_de_metadata_va_junto_al_mp4_con_el_mismo_nombre(tmp_path):
    mp4 = tmp_path / "0001_un_titulo.mp4"
    mp4.write_bytes(b"")
    path = write_metadata(_clip(), mp4)
    assert path == tmp_path / "0001_un_titulo.txt"
    assert "Un título con gancho" in path.read_text(encoding="utf-8")
