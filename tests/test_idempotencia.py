"""Tests de idempotencia del pipeline: procesar dos veces no duplica ni pierde.

Seams bajo test: las transiciones de ``State`` que deciden qué se re-hace
(``requeue_failed``, ``video_has_clips``, ``clip_uploaded``) y el orquestador
``cmd_process`` en el escenario que la auditoría señaló como blocker: una
corrida que murió entre seleccionar y renderizar no puede dejar clips
huérfanos ni re-seleccionar contra su propio dedup.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from aurclips import render as render_mod
from aurclips import select_clips as select_mod
from aurclips.__main__ import cmd_process
from aurclips.config import Config
from aurclips.state import State


def _clip(start=0.0, end=30.0, title="Un título", score=1.0):
    return SimpleNamespace(start=start, end=end, title=title,
                           description="desc", tags=["gaming"],
                           score=score, marked=False)


def _db_with_video(status: str = "transcribed") -> tuple[State, int]:
    db = State(":memory:")
    vid = db.add_video("local", "grabacion.mp4", "grabación",
                       "grabacion.mp4", 600.0)
    if status != "new":
        db.video_transcribed(vid)
    if status == "failed":
        db.video_failed(vid, "algo")
    return db, vid


# --- la guarda contra re-seleccionar ---------------------------------------

def test_un_video_con_clips_ya_tiene_clips(tmp_path):
    db, vid = _db_with_video()
    assert not db.video_has_clips(vid)
    db.add_clip(vid, 0, _clip(), "texto del clip")
    assert db.video_has_clips(vid)


def test_requeue_de_video_con_clips_va_a_selected():
    """Re-seleccionar con clips existentes es el camino de los huérfanos."""
    db, vid = _db_with_video("failed")
    db.add_clip(vid, 0, _clip(), "texto")
    db.requeue_failed(lambda _: True, lambda _: True)
    video = db.recent_videos(1)[0]
    assert video["status"] == "selected"


def test_requeue_de_video_sin_clips_va_a_transcribed_o_new():
    db, vid = _db_with_video("failed")
    db.requeue_failed(lambda _: True, lambda _: True)
    assert db.recent_videos(1)[0]["status"] == "transcribed"
    db.video_failed(vid, "otra vez")
    db.requeue_failed(lambda _: False, lambda _: True)
    assert db.recent_videos(1)[0]["status"] == "new"


# --- el retry comprueba archivos, no columnas -------------------------------

def test_un_clip_failed_sin_mp4_en_disco_vuelve_a_pending():
    """Con el mp4 borrado, 'rendered' sería el bucle failed→rendered→failed."""
    db, vid = _db_with_video()
    clip_id = db.add_clip(vid, 0, _clip(), "texto")
    db.clip_rendered(clip_id, "salida/0001_borrado.mp4")
    db.clip_failed(clip_id, "archivo inexistente")
    db.requeue_failed(lambda _: True, lambda path: False)
    assert db.recent_clips(1)[0]["status"] == "pending"


def test_un_clip_failed_con_mp4_en_disco_vuelve_a_rendered():
    db, vid = _db_with_video()
    clip_id = db.add_clip(vid, 0, _clip(), "texto")
    db.clip_rendered(clip_id, "salida/0001_vivo.mp4")
    db.clip_failed(clip_id, "la subida falló")
    db.requeue_failed(lambda _: True, lambda path: True)
    assert db.recent_clips(1)[0]["status"] == "rendered"


# --- subir escribe clip y hueco juntos --------------------------------------

def test_clip_uploaded_registra_clip_y_hueco_en_una_llamada():
    """El paso no reversible: tras clip_uploaded no queda nada por escribir."""
    db, vid = _db_with_video()
    clip_id = db.add_clip(vid, 0, _clip(), "texto")
    db.clip_rendered(clip_id, "salida/0001.mp4")
    db.clip_uploaded(clip_id, "yt123", "2026-07-24T19:00:00-04:00")
    fila = db.recent_clips(1)[0]
    assert fila["status"] == "uploaded"
    assert fila["youtube_id"] == "yt123"
    assert db.last_publish_at() == "2026-07-24T19:00:00-04:00"


# --- el escenario del blocker contra cmd_process ----------------------------

def _cfg(tmp_path: Path) -> Config:
    doc = {"paths": {"data": str(tmp_path / "data"),
                     "work": str(tmp_path / "work"),
                     "output": str(tmp_path / "output")},
           "titles": {"engine": "heuristic", "url": "http://127.0.0.1:9"}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return Config(tmp_path / "config.yaml")


def test_una_corrida_muerta_tras_seleccionar_no_deja_huerfanos(tmp_path, monkeypatch):
    """Corrida 1 murió entre add_clip y video_selected. La corrida 2 NO debe
    re-seleccionar (el dedup tacharía todo contra los clips propios y el video
    quedaría 'done' con los pendientes huérfanos): debe renderizar lo que hay.
    """
    cfg = _cfg(tmp_path)
    db, vid = _db_with_video("transcribed")
    db.add_clip(vid, 0, _clip(0, 30, "Clip uno"), "texto uno")
    db.add_clip(vid, 1, _clip(60, 90, "Clip dos"), "texto dos")

    # el transcript que cmd_process va a leer del workdir
    transcript = {"language": "es", "segments": []}
    tdir = cfg.work_dir / f"video_{vid}"
    tdir.mkdir(parents=True)
    (tdir / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")

    monkeypatch.setattr(select_mod, "select_clips",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("re-seleccionó: camino de huérfanos")))
    rendered = []

    def fake_render(cfg_, path, start, end, title, words, clip_id, **kw):
        rendered.append(clip_id)
        out = tmp_path / f"{clip_id}.mp4"
        out.write_bytes(b"")
        return out

    monkeypatch.setattr(render_mod, "render_clip", fake_render)

    cmd_process(cfg, db)

    assert len(rendered) == 2, "los clips pendientes no se renderizaron"
    assert db.recent_videos(1)[0]["status"] == "done"
    clips = db.recent_clips(5)
    assert len(clips) == 2, "aparecieron clips duplicados"
    assert all(c["status"] == "rendered" for c in clips)
