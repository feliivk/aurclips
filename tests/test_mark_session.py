"""Tests de la lógica de sesión de marcado.

Seam bajo test: ``MarkingSession`` (marcas preexistentes + eventos de la
sesión -> contenido del sidecar). Es la única costura nueva del repaso: el
transporte con mpv le pasa eventos y esto decide qué queda escrito.

Las aserciones sobre el archivo van por round-trip con el lector real
(``file_marks``/``parse_timecode``), nunca comparando strings: un repaso jamás
puede producir un sidecar que el selector no entienda.
"""

from pathlib import Path

from aurclips.marks import MarkingSession, file_marks, sidecar_path


def _written(tmp_path: Path, session: MarkingSession) -> list[float]:
    """Escribe el sidecar de la sesión y lo vuelve a leer con el lector real."""
    video = tmp_path / "grabacion.mp4"
    sidecar_path(video).write_text(session.sidecar_text("grabacion"),
                                   encoding="utf-8")
    return file_marks(video)


# --- sesión sobre video virgen ---------------------------------------------

def test_las_marcas_de_la_sesion_quedan_en_orden(tmp_path):
    session = MarkingSession([])
    session.mark(95.0)
    session.mark(30.0)  # marcaste tarde y rebobinaste: el archivo ordena igual
    session.mark(60.0)
    assert _written(tmp_path, session) == [30.0, 60.0, 95.0]


def test_una_sesion_sin_marcas_produce_un_sidecar_vacio_pero_valido(tmp_path):
    assert _written(tmp_path, MarkingSession([])) == []


# --- fusión con marcas preexistentes ---------------------------------------

def test_las_marcas_viejas_se_conservan_todas(tmp_path):
    session = MarkingSession([10.0, 200.0])
    session.mark(100.0)
    assert _written(tmp_path, session) == [10.0, 100.0, 200.0]


def test_repasar_sin_marcar_nada_no_pierde_lo_viejo(tmp_path):
    session = MarkingSession([45.0, 90.0])
    assert _written(tmp_path, session) == [45.0, 90.0]


# --- dedup de marcas casi simultáneas --------------------------------------

def test_el_enter_nervioso_no_duplica_la_marca(tmp_path):
    session = MarkingSession([])
    assert session.mark(60.0) is not None
    assert session.mark(60.4) is None  # a menos de 1 s: se funde
    assert _written(tmp_path, session) == [60.0]


def test_marcar_encima_de_una_vieja_tampoco_duplica(tmp_path):
    session = MarkingSession([60.0])
    assert session.mark(60.8) is None
    assert _written(tmp_path, session) == [60.0]


def test_dos_marcas_separadas_de_verdad_si_cuentan(tmp_path):
    session = MarkingSession([])
    session.mark(60.0)
    assert session.mark(61.5) is not None
    assert _written(tmp_path, session) == [60.0, 61.5]


# --- deshacer ---------------------------------------------------------------

def test_deshacer_quita_la_ultima_de_la_sesion(tmp_path):
    session = MarkingSession([])
    session.mark(30.0)
    session.mark(90.0)
    assert session.undo() == 90.0
    assert _written(tmp_path, session) == [30.0]


def test_deshacer_nunca_toca_las_preexistentes(tmp_path):
    session = MarkingSession([30.0])
    session.mark(90.0)
    assert session.undo() == 90.0
    assert session.undo() is None  # la sesión quedó vacía: no hay qué deshacer
    assert _written(tmp_path, session) == [30.0]


def test_deshacer_con_la_sesion_vacia_no_hace_nada(tmp_path):
    session = MarkingSession([15.0])
    assert session.undo() is None
    assert _written(tmp_path, session) == [15.0]


# --- el formato es el del hotkey de siempre --------------------------------

def test_el_sidecar_lleva_cabecera_de_comentario(tmp_path):
    session = MarkingSession([])
    session.mark(75.0)
    text = session.sidecar_text("mi partida")
    assert text.splitlines()[0].startswith("#")
    assert "mi partida" in text.splitlines()[0]


def test_los_tiempos_largos_sobreviven_el_round_trip(tmp_path):
    """Más de una hora: 75:30 en MM:SS extendido, como la sesión en vivo."""
    session = MarkingSession([])
    session.mark(75 * 60 + 30)
    assert _written(tmp_path, session) == [4530.0]


def test_una_marca_preexistente_con_fraccion_no_se_redondea(tmp_path):
    """Un `90.5` escrito a mano sobrevive al repaso: conservar es conservar."""
    session = MarkingSession([90.5])
    session.mark(30.0)
    assert _written(tmp_path, session) == [30.0, 90.5]


def test_la_precision_de_marcar_en_pausa_se_conserva(tmp_path):
    """La marca en pausa cae donde está el cursor, no en el segundo entero."""
    session = MarkingSession([])
    session.mark(7.24)
    assert _written(tmp_path, session) == [7.24]
