"""Tests de observabilidad: la primera pregunta del pipeline desatendido.

Seams bajo test: ``last_run_info`` (¿anoche corrió y cómo terminó?),
``local_youtube_id`` (el id sin red — lo que hace que una URL ya bajada
funcione offline), ``dir_size`` y el smoke del doctor (compone sondas, nunca
revienta).
"""

import os
import time
from pathlib import Path

import pytest
import yaml

from aurclips import ingest
from aurclips.config import Config
from aurclips.ingest import local_youtube_id, url_download
from aurclips.runner import dir_size, last_run_info


# --- última corrida ---------------------------------------------------------

def test_sin_corridas_no_hay_ultima(tmp_path):
    assert last_run_info(tmp_path) is None


def test_una_corrida_completa_se_reporta_bien(tmp_path):
    log = tmp_path / "run_2026-07-23_0300.log"
    log.write_text("...\nCorrida completa.\n", encoding="utf-8")
    assert last_run_info(tmp_path) == (log.name, True)


def test_una_corrida_muerta_se_reporta_incompleta(tmp_path):
    log = tmp_path / "run_2026-07-23_0300.log"
    log.write_text("Transcribiendo...\nTraceback (most recent call last):\n",
                   encoding="utf-8")
    nombre, ok = last_run_info(tmp_path)
    assert nombre == log.name and not ok


def test_gana_el_log_mas_reciente_aunque_sea_de_watch(tmp_path):
    viejo = tmp_path / "run_2026-07-22_0300.log"
    viejo.write_text("Corrida completa.\n", encoding="utf-8")
    stamp = time.time() - 3600
    os.utime(viejo, (stamp, stamp))
    nuevo = tmp_path / "watch_2026-07-23_120000.log"
    nuevo.write_text("vigilando...\n[watch] detenido; el estado queda guardado",
                     encoding="utf-8")
    assert last_run_info(tmp_path) == (nuevo.name, True)


# --- el id de YouTube sin red -----------------------------------------------

def test_los_formatos_estandar_de_youtube_se_parsean_sin_red():
    assert local_youtube_id(
        "https://www.youtube.com/watch?v=9_FBT46QqSA&t=51s") == "9_FBT46QqSA"
    assert local_youtube_id("https://youtu.be/9_FBT46QqSA") == "9_FBT46QqSA"
    assert local_youtube_id(
        "https://youtube.com/shorts/9_FBT46QqSA") == "9_FBT46QqSA"
    assert local_youtube_id(
        "https://www.youtube.com/live/9_FBT46QqSA?feature=share") == "9_FBT46QqSA"


def test_otras_urls_no_se_adivinan():
    assert local_youtube_id("https://vimeo.com/12345") is None
    assert local_youtube_id("https://youtube.com/@canal/videos") is None


def test_una_url_ya_descargada_funciona_sin_internet(tmp_path, monkeypatch):
    """El momento donde la caché más vale: con el archivo en disco, el verbo
    entero corre offline — el sondeo de red ni se intenta."""
    doc = {"paths": {"downloads": str(tmp_path / "descargas")}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    cfg = Config(tmp_path / "config.yaml")
    video = cfg.downloads_dir / "9_FBT46QqSA.mp4"
    video.write_bytes(b"contenido")

    monkeypatch.setattr(ingest, "_probe_url_id",
                        lambda cfg, url: pytest.fail("tocó la red sin necesidad"))
    resolved = url_download(cfg, "https://youtu.be/9_FBT46QqSA")
    assert resolved == video


# --- tamaños ----------------------------------------------------------------

def test_dir_size_suma_lo_que_hay(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.bin").write_bytes(b"x" * 100)
    (tmp_path / "sub" / "b.bin").write_bytes(b"x" * 50)
    assert dir_size(tmp_path) == 150
    assert dir_size(tmp_path / "no_existe") == 0


# --- el doctor nunca revienta ------------------------------------------------

def test_doctor_reporta_aunque_falte_todo(tmp_path, capsys, monkeypatch):
    """En una máquina pelada, doctor describe el estado; jamás lanza."""
    from aurclips.__main__ import cmd_doctor
    from aurclips.state import State

    doc = {"paths": {"data": str(tmp_path / "data"),
                     "work": str(tmp_path / "work"),
                     "output": str(tmp_path / "output"),
                     "logs": str(tmp_path / "logs"),
                     "downloads": str(tmp_path / "descargas"),
                     "ffmpeg": str(tmp_path / "sin_binarios")}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    cfg = Config(tmp_path / "config.yaml")
    db = State(":memory:")

    import shutil as shutil_mod
    monkeypatch.setattr(shutil_mod, "which", lambda name: None)

    cmd_doctor(cfg, db)  # no debe lanzar pase lo que pase
    out = capsys.readouterr().out
    assert "Dependencias" in out
    assert "FALTA" in out          # ffmpeg no está: se dice, no se explota
    assert "Última corrida" in out
    assert "ninguna todavía" in out
    assert "Colas" in out
    assert "libre en disco" in out
