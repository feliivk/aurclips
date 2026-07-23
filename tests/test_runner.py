"""Tests del andamiaje de la corrida diaria.

Seams bajo test: ``prune_run_logs`` (rotación, pura) y ``single_instance``
(exclusión entre corridas). Es lo que se movió de run.ps1 al CLI para que la
automatización sea igual en los tres SO.
"""

import sys
from pathlib import Path

from aurclips.runner import prune_run_logs, single_instance, tee_output


def _make_logs(log_dir: Path, n: int) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (log_dir / f"run_2026-01-{i + 1:02d}_0300.log").write_text("x", encoding="utf-8")


def test_la_rotacion_conserva_los_mas_nuevos(tmp_path):
    _make_logs(tmp_path, 35)
    removed = prune_run_logs(tmp_path, keep=30)
    quedan = sorted(p.name for p in tmp_path.glob("run_*.log"))
    assert removed == 5
    assert len(quedan) == 30
    # se quedaron los últimos, no los primeros
    assert quedan[0] == "run_2026-01-06_0300.log"
    assert quedan[-1] == "run_2026-01-35_0300.log"


def test_por_debajo_del_tope_no_borra_nada(tmp_path):
    _make_logs(tmp_path, 10)
    assert prune_run_logs(tmp_path, keep=30) == 0
    assert len(list(tmp_path.glob("run_*.log"))) == 10


def test_no_toca_otros_archivos(tmp_path):
    _make_logs(tmp_path, 3)
    (tmp_path / "events.log").write_text("importante", encoding="utf-8")
    prune_run_logs(tmp_path, keep=0)  # keep=0 borra todos los run_*
    assert (tmp_path / "events.log").exists()
    assert not list(tmp_path.glob("run_*.log"))


def test_una_sola_instancia_a_la_vez(tmp_path):
    """La segunda corrida sobre el mismo lock no lo consigue."""
    lock = tmp_path / "run.lock"
    with single_instance(lock) as primera:
        assert primera is True
        with single_instance(lock) as segunda:
            assert segunda is False


def test_el_lock_se_libera_al_salir(tmp_path):
    """Tras cerrar la primera, otra corrida sí puede adquirirlo."""
    lock = tmp_path / "run.lock"
    with single_instance(lock) as a:
        assert a is True
    with single_instance(lock) as b:
        assert b is True


def test_tee_escribe_en_consola_y_archivo(tmp_path, capsys):
    log = tmp_path / "run_test.log"
    with tee_output(log):
        print("hola corrida")
    assert "hola corrida" in capsys.readouterr().out
    assert "hola corrida" in log.read_text(encoding="utf-8")


def test_tee_sigue_pareciendo_un_stream(tmp_path):
    """Delega isatty/encoding a la consola: si faltaran, una barra de progreso
    del pipeline rompería la corrida entera al consultarlos."""
    from aurclips.runner import _Tee

    log = tmp_path / "run.log"
    with open(log, "w", encoding="utf-8") as f:
        tee = _Tee(sys.__stdout__, f)
        # no revientan (AttributeError sería el bug):
        assert isinstance(tee.isatty(), bool)
        assert tee.encoding == sys.__stdout__.encoding
