"""Tests del verbo de YouTube: URLs que se resuelven a archivos locales.

Seams bajo test: ``is_url`` (qué cuenta como URL), ``find_downloaded`` (la
reutilización por id) y el corto-circuito de ``url_download`` (una URL ya
bajada no se vuelve a bajar — la promesa que hace barato mark URL + clip URL).
Las llamadas de red de yt-dlp quedan fuera, como ffmpeg y mpv.
"""

from pathlib import Path

import pytest
import yaml

from aurclips import ingest
from aurclips.config import Config
from aurclips.ingest import find_downloaded, is_url, url_download


def _cfg(tmp_path: Path) -> Config:
    doc = {"paths": {"downloads": str(tmp_path / "descargas")}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return Config(tmp_path / "config.yaml")


# --- qué cuenta como URL ----------------------------------------------------

def test_las_urls_con_esquema_cuentan():
    assert is_url("https://www.youtube.com/watch?v=9_FBT46QqSA&t=51s")
    assert is_url("http://ejemplo.com/video")


def test_rutas_y_nombres_no_cuentan():
    assert not is_url("data/pruebas/partida.mp4")
    assert not is_url(r"C:\videos\partida.mp4")
    assert not is_url("mi-sesion-de-marcado")
    assert not is_url("www.youtube.com/watch?v=x")  # sin esquema: es una ruta


# --- reutilización por id ---------------------------------------------------

def test_encuentra_la_descarga_por_id(tmp_path):
    cfg = _cfg(tmp_path)
    video = cfg.downloads_dir / "9_FBT46QqSA.mp4"
    video.write_bytes(b"")
    assert find_downloaded(cfg, "9_FBT46QqSA") == video


def test_el_sidecar_de_marcas_no_se_confunde_con_el_video(tmp_path):
    cfg = _cfg(tmp_path)
    (cfg.downloads_dir / "9_FBT46QqSA.marks.txt").write_text("# marcas",
                                                             encoding="utf-8")
    assert find_downloaded(cfg, "9_FBT46QqSA") is None


def test_sin_descarga_devuelve_none(tmp_path):
    assert find_downloaded(_cfg(tmp_path), "9_FBT46QqSA") is None


def test_otro_contenedor_tambien_vale(tmp_path):
    """Las descargas de canales pueden quedar en mkv/webm: mismo id, sirve."""
    cfg = _cfg(tmp_path)
    video = cfg.downloads_dir / "9_FBT46QqSA.webm"
    video.write_bytes(b"")
    assert find_downloaded(cfg, "9_FBT46QqSA") == video


# --- la promesa: una URL bajada no se vuelve a bajar ------------------------

def test_una_url_ya_descargada_no_se_vuelve_a_bajar(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    video = cfg.downloads_dir / "9_FBT46QqSA.mp4"
    video.write_bytes(b"contenido")

    monkeypatch.setattr(ingest, "_probe_url_id",
                        lambda cfg, url: "9_FBT46QqSA")
    monkeypatch.setattr(ingest, "download_video",
                        lambda *a, **k: pytest.fail("no debería descargar"))
    resolved = url_download(cfg, "https://youtube.com/watch?v=9_FBT46QqSA")
    assert resolved == video


def test_una_url_ilegible_da_un_error_de_una_linea(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "_probe_url_id", lambda cfg, url: None)
    with pytest.raises(ValueError) as exc:
        url_download(_cfg(tmp_path), "https://youtube.com/watch?v=roto")
    assert "URL" in str(exc.value) or "url" in str(exc.value)
