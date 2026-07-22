"""Tests de la cabecera de datos que ve `review`.

Seam bajo test: ``review_header`` (base de estado -> 2-3 líneas). Lo que se
afirma es la promesa del bucle de datos: en el momento de decidir se ve el
ganador de cada dimensión, y **solo** cuando hay muestra para afirmarlo.
"""

from pathlib import Path

from aurclips.state import State
from aurclips.stats import MIN_SAMPLE, review_header


def _db(tmp_path: Path, clips: list[tuple[float, str, bool, int]]) -> State:
    """Base con clips ya publicados: (duración, título, marcado, vistas)."""
    db = State(tmp_path / "state.db")
    vid = db.add_video("local", "grabacion.mp4", "grabación", "grabacion.mp4", 600)
    for i, (dur, title, marked, views) in enumerate(clips):
        start = i * 100.0
        clip_id = db.add_clip(vid, i, start, start + dur, title, "desc", [],
                              marked=marked)
        db.update_clip(clip_id, status="uploaded", views=views, likes=0)
    return db


CORTO_Y_BUENO = [(25.0, f"El error numero {i}", True, 1000) for i in range(4)]
LARGO_Y_FLOJO = [(55.0, f"Hablemos del tema {i}?", False, 100) for i in range(4)]


def test_bajo_la_muestra_minima_no_se_compara_nada(tmp_path):
    # con n=4 un promedio sesga la decisión justo cuando más pesa: se dice
    # que no hay datos, no se insinúa una tendencia
    lineas = review_header(_db(tmp_path, CORTO_Y_BUENO))
    assert len(lineas) == 1
    assert "sin datos" in lineas[0]
    assert "4" in lineas[0]
    texto = " ".join(lineas)
    assert "duración" not in texto and "vistas de media" not in texto


def test_con_muestra_suficiente_sale_el_ganador_de_cada_dimension(tmp_path):
    lineas = review_header(_db(tmp_path, CORTO_Y_BUENO + LARGO_Y_FLOJO))
    texto = " ".join(lineas)
    assert f"{MIN_SAMPLE + 2} Shorts publicados" in lineas[0]
    assert "21-35 s" in texto            # gana la duración que rinde
    assert "marcados por ti" in texto    # y el origen que rinde
    assert "más de 50 s" not in texto    # solo el ganador, no la tabla


def test_la_cabecera_cabe_en_la_pantalla(tmp_path):
    # en review se decide, no se analiza: título + una línea por dimensión
    lineas = review_header(_db(tmp_path, CORTO_Y_BUENO + LARGO_Y_FLOJO))
    assert len(lineas) <= 4
    assert all(len(línea) < 80 for línea in lineas)
