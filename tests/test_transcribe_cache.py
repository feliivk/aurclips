"""Tests de la caché de transcripciones.

Seam bajo test: ``content_key`` (identidad de una grabación) y el par
``store_transcript`` / ``cached_transcript``. Lo que se afirma es la promesa:
el mismo contenido no se vuelve a transcribir aunque cambie de nombre o de
carpeta, y un modelo distinto no reutiliza el trabajo de otro.

Sin Whisper y sin GPU: aquí no se transcribe nada, solo se guarda y se busca.
"""

from pathlib import Path

import pytest
import yaml

from aurclips import transcribe as T
from aurclips.config import Config

TRANSCRIPT = {
    "language": "es",
    "segments": [
        {"start": 0.0, "end": 2.0, "text": "esto es un short",
         "words": [{"start": 0.0, "end": 0.5, "word": "esto"},
                   {"start": 0.5, "end": 1.0, "word": "es"},
                   {"start": 1.0, "end": 1.5, "word": "un"},
                   {"start": 1.5, "end": 2.0, "word": "short"}]},
    ],
}


def _cfg(tmp_path: Path, **whisper) -> Config:
    doc = {"paths": {"work": str(tmp_path / "work")}, "whisper": whisper}
    path = tmp_path / f"config_{len(whisper)}_{whisper.get('model', 'x')}.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return Config(path)


def _video(path: Path, content: bytes = b"contenido de video " * 1000) -> Path:
    path.write_bytes(content)
    return path


# --- la identidad de una grabación -----------------------------------------

def test_el_mismo_contenido_con_otro_nombre_tiene_la_misma_clave(tmp_path):
    """Renombrar o mover una grabación no la convierte en otra."""
    cfg = _cfg(tmp_path, model="medium")
    original = _video(tmp_path / "partida.mp4")
    movido = _video(tmp_path / "otra_carpeta_renombrado.mp4")
    assert T.content_key(cfg, original) == T.content_key(cfg, movido)


def test_contenido_distinto_da_clave_distinta(tmp_path):
    cfg = _cfg(tmp_path, model="medium")
    una = _video(tmp_path / "una.mp4", b"grabacion de la partida uno" * 500)
    otra = _video(tmp_path / "otra.mp4", b"grabacion de la partida dos" * 500)
    assert T.content_key(cfg, una) != T.content_key(cfg, otra)


def test_dos_grabaciones_del_mismo_tamano_no_comparten_clave(tmp_path):
    """El tamaño no basta como identidad: el contenido también cuenta."""
    cfg = _cfg(tmp_path, model="medium")
    una = _video(tmp_path / "una.mp4", b"A" * 4096)
    otra = _video(tmp_path / "otra.mp4", b"B" * 4096)
    assert T.content_key(cfg, una) != T.content_key(cfg, otra)


def test_cambiar_de_modelo_de_whisper_cambia_la_clave(tmp_path):
    """Bajar de 'medium' a 'small' no puede servir la transcripción vieja."""
    video = _video(tmp_path / "partida.mp4")
    con_medium = T.content_key(_cfg(tmp_path, model="medium"), video)
    con_small = T.content_key(_cfg(tmp_path, model="small"), video)
    assert con_medium != con_small


def test_cambiar_de_idioma_forzado_cambia_la_clave(tmp_path):
    video = _video(tmp_path / "partida.mp4")
    automatico = T.content_key(_cfg(tmp_path, model="medium"), video)
    forzado = T.content_key(_cfg(tmp_path, model="medium", language="en"), video)
    assert automatico != forzado


def test_calcular_la_clave_no_lee_la_grabacion_entera(tmp_path):
    """Hashear varios GB tardaría más que lo que ahorra: se muestrea.

    Se afirma por su consecuencia, que es la única forma honesta de verlo
    desde fuera: si de verdad solo se leen muestras, cambiar bytes de una zona
    que no se muestrea no cambia la clave. Es el precio del muestreo y está
    documentado: dos archivos así son un recodificado, no otra grabación.
    """
    cfg = _cfg(tmp_path, model="medium")
    size = 8 * T.SAMPLE_BYTES
    contenido = bytearray(b"x" * size)
    original = _video(tmp_path / "grande.mp4", bytes(contenido))

    # zona intermedia intacta por las tres muestras (principio, centro, final)
    sin_muestrear = int(T.SAMPLE_BYTES * 1.5)
    contenido[sin_muestrear:sin_muestrear + 1024] = b"z" * 1024
    retocado = _video(tmp_path / "retocado.mp4", bytes(contenido))

    assert T.content_key(cfg, original) == T.content_key(cfg, retocado)


# --- guardar y recuperar ----------------------------------------------------

def test_lo_guardado_se_recupera_igual(tmp_path):
    """Palabras y tiempos sobreviven intactos: las marcas por voz salen de ahí."""
    cfg = _cfg(tmp_path, model="medium")
    video = _video(tmp_path / "partida.mp4")
    T.store_transcript(cfg, video, TRANSCRIPT)
    assert T.cached_transcript(cfg, video) == TRANSCRIPT


def test_una_grabacion_nunca_vista_no_tiene_cache(tmp_path):
    cfg = _cfg(tmp_path, model="medium")
    assert T.cached_transcript(cfg, _video(tmp_path / "nueva.mp4")) is None


def test_la_cache_sirve_a_la_grabacion_renombrada(tmp_path):
    cfg = _cfg(tmp_path, model="medium")
    T.store_transcript(cfg, _video(tmp_path / "partida.mp4"), TRANSCRIPT)
    renombrada = _video(tmp_path / "partida_final_v2.mp4")
    assert T.cached_transcript(cfg, renombrada) == TRANSCRIPT


def test_otro_modelo_no_reutiliza_la_transcripcion(tmp_path):
    video = _video(tmp_path / "partida.mp4")
    T.store_transcript(_cfg(tmp_path, model="medium"), video, TRANSCRIPT)
    assert T.cached_transcript(_cfg(tmp_path, model="small"), video) is None


def test_una_cache_corrupta_no_rompe_la_corrida(tmp_path):
    """Un JSON a medias se ignora y se transcribe de nuevo, no revienta."""
    cfg = _cfg(tmp_path, model="medium")
    video = _video(tmp_path / "partida.mp4")
    T.store_transcript(cfg, video, TRANSCRIPT)
    T.cache_path(cfg, T.content_key(cfg, video)).write_text("{roto", encoding="utf-8")
    assert T.cached_transcript(cfg, video) is None


# --- lo que la caché le ahorra al pipeline ----------------------------------

def test_con_cache_no_se_carga_whisper(tmp_path, monkeypatch):
    """La promesa entera: si ya está transcrito, el modelo ni se toca."""
    cfg = _cfg(tmp_path, model="medium")
    video = _video(tmp_path / "partida.mp4")
    T.store_transcript(cfg, video, TRANSCRIPT)

    def explota(*args, **kwargs):
        raise AssertionError("no debería cargarse Whisper con la caché caliente")

    monkeypatch.setattr(T, "_get_model", explota)
    assert T.transcribe(cfg, str(video)) == TRANSCRIPT


def test_transcribir_deja_tambien_el_json_que_le_piden(tmp_path, monkeypatch):
    """El pipeline sigue teniendo su transcript.json donde lo espera."""
    cfg = _cfg(tmp_path, model="medium")
    video = _video(tmp_path / "partida.mp4")
    T.store_transcript(cfg, video, TRANSCRIPT)
    destino = tmp_path / "work" / "video_1" / "transcript.json"

    monkeypatch.setattr(T, "_get_model", lambda *a, **k: pytest.fail("sin caché"))
    T.transcribe(cfg, str(video), destino)
    assert destino.exists()
