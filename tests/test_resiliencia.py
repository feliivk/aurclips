"""Tests de resiliencia: la corrida degrada con una línea, nunca muere muda.

Seams bajo test: la conversión de fallos de infraestructura en RuntimeError
curado (sesión de YouTube, modelo de Whisper) y el blindaje de cmd_run — una
corrida que revienta deja su traza EN el run log y su evento en events.log.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from aurclips import transcribe as T
from aurclips.config import Config
from aurclips.state import State


# --- el refresh de sesión degrada, no revienta ------------------------------

def test_un_refresh_sin_internet_se_vuelve_runtime_error(tmp_path, monkeypatch):
    """TransportError de google.auth no es RuntimeError: sin la conversión,
    la corrida programada con token caducado moría con traza."""
    from aurclips import upload

    doc = {"paths": {"credentials": str(tmp_path / "creds")}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    cfg = Config(tmp_path / "config.yaml")
    (tmp_path / "creds").mkdir(exist_ok=True)
    (tmp_path / "creds" / "youtube_token.json").write_text("{}", encoding="utf-8")

    class _Creds:
        expired = True
        refresh_token = "algo"
        valid = False

        def refresh(self, request):
            raise ConnectionError("getaddrinfo failed")  # sin red

    import google.oauth2.credentials as gcreds
    monkeypatch.setattr(gcreds.Credentials, "from_authorized_user_file",
                        classmethod(lambda cls, *a, **k: _Creds()))
    with pytest.raises(RuntimeError) as exc:
        upload.get_credentials(cfg, interactive=False)
    assert "quedan en cola" in str(exc.value)


def test_upload_pending_degrada_ante_cualquier_fallo_de_sesion(tmp_path, monkeypatch):
    """El pre-chequeo atrapa lo que sea: la cola se conserva y devuelve 0."""
    from aurclips import upload

    doc = {"upload": {"enabled": True}, "review": {"enabled": False},
           "paths": {"data": str(tmp_path / "data")}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    cfg = Config(tmp_path / "config.yaml")
    db = State(":memory:")
    vid = db.add_video("local", "v.mp4", "v", "v.mp4", 100.0)
    clip_id = db.add_clip(vid, 0, SimpleNamespace(
        start=0.0, end=30.0, title="t", description="", tags=[],
        score=1.0, marked=False), "texto")
    db.clip_rendered(clip_id, "salida.mp4")

    def explota(cfg_, interactive=True):
        raise ConnectionError("la red se fue")  # ni siquiera RuntimeError

    monkeypatch.setattr(upload, "get_credentials", explota)
    assert upload.upload_pending(cfg, db) == 0
    assert db.recent_clips(1)[0]["status"] == "rendered"  # cola intacta


# --- el modelo de Whisper sin internet: línea útil, no traza ----------------

def test_modelo_ausente_sin_internet_da_mensaje_util(tmp_path, monkeypatch):
    doc = {"paths": {"work": str(tmp_path / "work")},
           "whisper": {"model": "medium"}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    cfg = Config(tmp_path / "config.yaml")

    class _Boom:
        def __init__(self, *a, **k):
            raise OSError("Cannot find an appropriate cached snapshot")

    import faster_whisper
    monkeypatch.setattr(faster_whisper, "WhisperModel", _Boom)
    T._model_cache.clear()
    monkeypatch.setattr(T, "_cuda_available", lambda: False)
    with pytest.raises(RuntimeError) as exc:
        T._get_model(cfg)
    assert "internet" in str(exc.value).lower()
    T._model_cache.clear()


def test_un_fallo_de_gpu_no_se_disfraza_de_falta_de_modelo(tmp_path, monkeypatch):
    """Un OSError con pinta de CUDA se relanza tal cual: la ruta GPU->CPU de
    transcribe() es quien lo maneja, no el mensaje de 'sin internet'."""
    doc = {"paths": {"work": str(tmp_path / "work")}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    cfg = Config(tmp_path / "config.yaml")

    class _Boom:
        def __init__(self, *a, **k):
            raise OSError("libcudnn_ops_infer.so.8: cannot open shared object file")

    import faster_whisper
    monkeypatch.setattr(faster_whisper, "WhisperModel", _Boom)
    T._model_cache.clear()
    monkeypatch.setattr(T, "_cuda_available", lambda: True)
    with pytest.raises(OSError) as exc:
        T._get_model(cfg)
    assert not isinstance(exc.value, RuntimeError)
    T._model_cache.clear()


# --- una corrida muerta deja rastro -----------------------------------------

def test_una_corrida_que_muere_deja_traza_en_el_log_y_evento(tmp_path, monkeypatch):
    from aurclips import __main__ as cli

    doc = {"paths": {"data": str(tmp_path / "data"),
                     "logs": str(tmp_path / "logs")}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    cfg = Config(tmp_path / "config.yaml")
    db = State(":memory:")

    monkeypatch.setattr(cli, "_run_pipeline",
                        lambda c, d: (_ for _ in ()).throw(
                            OSError("el disco se llenó")))
    with pytest.raises(SystemExit) as exc:
        cli.cmd_run(cfg, db)
    assert exc.value.code == 1

    run_logs = list(Path(tmp_path / "logs").glob("run_*.log"))
    assert run_logs, "no quedó log de la corrida muerta"
    contenido = run_logs[0].read_text(encoding="utf-8")
    assert "el disco se llenó" in contenido, "la traza no quedó en el run log"
    eventos = (tmp_path / "logs" / "events.log").read_text(encoding="utf-8")
    assert "La corrida murió" in eventos, "el evento de error no se registró"
