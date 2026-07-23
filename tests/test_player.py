"""Tests de la resolución de mpv.

Solo la resolución: el transporte (proceso, IPC) queda fuera de los tests
automáticos a propósito, como ffmpeg. Aquí se afirma la convención de
binarios del repo — tools/ primero, PATH después, error orientado a instalar.
"""

import pytest

from aurclips import player
from aurclips.player import find_mpv


def test_el_empaquetado_en_tools_gana(tmp_path, monkeypatch):
    bundled = tmp_path / "tools" / "mpv" / "mpv"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"")
    monkeypatch.setattr(player, "ROOT", tmp_path)
    monkeypatch.setattr(player, "_exe_suffix", lambda: "")
    monkeypatch.setattr(player.shutil, "which", lambda name: "/usr/bin/mpv")
    assert find_mpv() == str(bundled)


def test_sin_empaquetado_cae_al_path(tmp_path, monkeypatch):
    monkeypatch.setattr(player, "ROOT", tmp_path)
    monkeypatch.setattr(player.shutil, "which", lambda name: "/usr/bin/mpv")
    assert find_mpv() == "/usr/bin/mpv"


def test_sin_mpv_el_error_orienta_a_instalar(tmp_path, monkeypatch):
    monkeypatch.setattr(player, "ROOT", tmp_path)
    monkeypatch.setattr(player.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError) as exc:
        find_mpv()
    msg = str(exc.value).lower()
    assert "mpv" in msg
    assert any(k in msg for k in ("brew", "apt", "winget"))