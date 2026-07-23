"""Tests de la localización de binarios externos (ffmpeg/ffprobe).

Seam bajo test: ``Config._tool``. Lo que se afirma: encuentra el binario
empaquetado con el nombre correcto para el SO, cae al PATH del sistema cuando
no hay empaquetado, y si no hay ninguno el error orienta a instalar por gestor
de paquetes — no a un script de Windows.

Sin ejecutar ffmpeg: solo se resuelven rutas.
"""

import os
from pathlib import Path

import yaml

from aurclips import config as config_mod
from aurclips.config import Config


# paths.ffmpeg absoluto: al ser absoluto, base/rel == rel para cualquier base,
# así que el fallback a la raíz del repo (que en este checkout SÍ trae un ffmpeg
# vendorizado) queda neutralizado y el test controla qué existe y qué no.
def _bin_dir(tmp_path: Path) -> Path:
    return tmp_path / "bin"


def _cfg(tmp_path: Path) -> Config:
    doc = {"paths": {"ffmpeg": str(_bin_dir(tmp_path))}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return Config(tmp_path / "config.yaml")


def _make_bundled(tmp_path: Path, filename: str) -> Path:
    d = _bin_dir(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    f = d / filename
    f.write_bytes(b"")
    return f


def test_el_binario_del_path_se_usa(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    cfg = _cfg(tmp_path)
    assert cfg.ffmpeg == "/usr/bin/ffmpeg"
    assert cfg.ffprobe == "/usr/bin/ffprobe"


def test_el_empaquetado_usa_exe_en_windows(tmp_path, monkeypatch):
    # se patchea el helper del sufijo, no os.name: tocar os.name rompe pathlib
    # (lo lee al construir cada Path para decidir Windows/Posix)
    monkeypatch.setattr(config_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(config_mod, "_exe_suffix", lambda: ".exe")
    bundled = _make_bundled(tmp_path, "ffmpeg.exe")
    cfg = _cfg(tmp_path)
    assert cfg.ffmpeg == str(bundled)


def test_el_empaquetado_no_usa_exe_en_posix(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(config_mod, "_exe_suffix", lambda: "")
    bundled = _make_bundled(tmp_path, "ffmpeg")
    cfg = _cfg(tmp_path)
    assert cfg.ffmpeg == str(bundled)


def test_sin_binario_el_error_orienta_a_instalar_no_a_setup_ps1(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod.shutil, "which", lambda name: None)
    cfg = _cfg(tmp_path)
    try:
        _ = cfg.ffmpeg
        assert False, "debería haber fallado sin ffmpeg"
    except FileNotFoundError as e:
        msg = str(e).lower()
        assert "setup.ps1" not in msg
        assert "ffmpeg" in msg
        # menciona al menos una vía de instalación multiplataforma
        assert any(k in msg for k in ("brew", "apt", "path", "winget"))
