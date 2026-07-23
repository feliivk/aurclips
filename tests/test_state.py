"""Tests del ciclo de vida de grabaciones y clips.

Seam bajo test: ``State`` (transiciones del dominio -> filas de SQLite). Lo que
se afirma es la promesa de los dos ejes: el progreso mecánico y tu criterio
avanzan por separado, y "listo para subir" es la combinación de los dos, en un
solo sitio.
"""

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from aurclips.state import SCHEMA, State


def _clip(start: float = 0.0, end: float = 30.0, title: str = "Un título",
          description: str = "Una descripción", tags=("gaming",),
          score: float = 1.0, marked: bool = False) -> SimpleNamespace:
    """Lo que el selector le entrega a ``add_clip``, sin traer al selector."""
    return SimpleNamespace(start=start, end=end, title=title,
                           description=description, tags=list(tags),
                           score=score, marked=marked)


def _db() -> tuple[State, int]:
    """Base en memoria con una grabación ya dada de alta."""
    db = State(":memory:")
    video_id = db.add_video("local", "grabacion.mp4", "grabación",
                            "grabacion.mp4", 600.0)
    return db, video_id


# --- el clip recién nacido ----------------------------------------------

def test_un_clip_recien_dado_de_alta_no_esta_listo_para_nada():
    """Alta = pendiente de renderizar y sin criterio tuyo: no está en ninguna cola."""
    db, video_id = _db()
    clip_id = db.add_clip(video_id, 0, _clip())
    assert [r["id"] for r in db.clips_to_render(video_id)] == [clip_id]
    assert db.clips_to_review() == []
    assert db.clips_to_upload(require_review=True) == []
    assert db.clips_to_upload(require_review=False) == []


def test_add_clip_guarda_los_tags_como_json():
    """El llamador entrega una lista de tags y recupera esa misma lista."""
    db, video_id = _db()
    tags = ["gaming", "aurclips", "comentario"]
    db.add_clip(video_id, 0, _clip(tags=tags))
    row = db.clips_for_video(video_id)[0]
    assert json.loads(row["tags"]) == tags


def test_los_tags_van_y_vuelven_sin_que_el_llamador_toque_json():
    """Serializar y deserializar son el mismo acto, y vive entero en el módulo."""
    db, video_id = _db()
    clip_id = db.add_clip(video_id, 0, _clip(tags=["uno", "dos"]))
    assert db.clip_tags(db.clips_for_video(video_id)[0]) == ["uno", "dos"]
    db.clip_rendered(clip_id, "clip.mp4")
    db.clip_approved(clip_id, "Título", "Descripción", ["tres"])
    assert db.clip_tags(db.clips_for_video(video_id)[0]) == ["tres"]


# --- el recorrido feliz --------------------------------------------------

def test_el_recorrido_feliz_lleva_el_clip_de_la_cola_a_publicado():
    """Renderizar, aprobar y subir mueven el clip por las colas, una a una."""
    db, video_id = _db()
    clip_id = db.add_clip(video_id, 0, _clip())

    db.clip_rendered(clip_id, "clip.mp4")
    assert [r["id"] for r in db.clips_to_review()] == [clip_id]
    assert db.clips_to_upload(require_review=True) == []

    db.clip_approved(clip_id, "Título revisado", "Descripción revisada",
                     ["gaming"])
    assert db.clips_to_review() == []
    assert [r["id"] for r in db.clips_to_upload(require_review=True)] == [clip_id]

    db.clip_uploaded(clip_id, "yt-123", "2026-07-23T18:00:00+00:00")
    assert db.clips_to_upload(require_review=True) == []
    publicados = db.published()
    assert [r["id"] for r in publicados] == [clip_id]
    # las correcciones de la revisión son las que se publican
    assert publicados[0]["title"] == "Título revisado"
    assert json.loads(publicados[0]["tags"]) == ["gaming"]
    assert [r["youtube_id"] for r in db.uploaded_with_youtube_id()] == ["yt-123"]


def test_un_clip_descartado_sale_de_las_dos_colas():
    """Descartar retira el clip de la revisión y de la subida revisada."""
    db, video_id = _db()
    clip_id = db.add_clip(video_id, 0, _clip())
    db.clip_rendered(clip_id, "clip.mp4")
    db.clip_discarded(clip_id)
    assert db.clips_to_review() == []
    assert db.clips_to_upload(require_review=True) == []


def test_sin_revision_se_sube_todo_lo_renderizado_incluido_lo_descartado():
    """Sin revisión la cola solo mira el progreso, y eso incluye lo descartado."""
    db, video_id = _db()
    approved = db.add_clip(video_id, 0, _clip())
    discarded = db.add_clip(video_id, 1, _clip())
    pending = db.add_clip(video_id, 2, _clip())
    db.clip_rendered(approved, "a.mp4")
    db.clip_rendered(discarded, "b.mp4")
    db.clip_approved(approved, "Título", "Descripción", [])
    db.clip_discarded(discarded)

    ids = [r["id"] for r in db.clips_to_upload(require_review=False)]
    # el descartado también sale: el pipeline desatendido ignora tu criterio.
    # Es una rareza de la conducta de hoy y se congela a propósito, para que
    # el día que deje de pasar sea una decisión y no un descuido.
    assert ids == [approved, discarded]
    assert pending not in ids


def test_despublicar_devuelve_el_clip_a_la_cola_de_revision():
    """Borrar el Short reabre el criterio y borra el rastro de la publicación."""
    db, video_id = _db()
    clip_id = db.add_clip(video_id, 0, _clip())
    db.clip_rendered(clip_id, "clip.mp4")
    db.clip_approved(clip_id, "Título", "Descripción", [])
    db.clip_uploaded(clip_id, "yt-123", "2026-07-23T18:00:00+00:00")

    db.clip_unpublished(clip_id)
    assert [r["id"] for r in db.clips_to_review()] == [clip_id]
    assert db.published() == []
    row = db.clips_for_video(video_id)[0]
    assert row["youtube_id"] is None
    assert row["publish_at"] is None


# --- la situación de un clip ---------------------------------------------

def test_la_situacion_combina_los_dos_ejes_en_un_solo_sitio():
    """Quien muestra un clip pregunta en qué punto está, no cómo se codifica."""
    db, video_id = _db()
    en_curso = db.add_clip(video_id, 0, _clip())
    por_revisar = db.add_clip(video_id, 1, _clip())
    descartado = db.add_clip(video_id, 2, _clip())
    publicado = db.add_clip(video_id, 3, _clip())
    db.clip_rendered(por_revisar, "a.mp4")
    db.clip_rendered(descartado, "b.mp4")
    db.clip_discarded(descartado)
    db.clip_rendered(publicado, "c.mp4")
    db.clip_approved(publicado, "Título", "Descripción", [])
    db.clip_uploaded(publicado, "yt-123", "2026-07-23T18:00:00+00:00")

    rows = {r["id"]: r for r in db.clips_for_video(video_id)}

    def situacion(clip_id: int, con_revision: bool = True) -> str:
        return db.clip_situation(rows[clip_id], con_revision)

    assert situacion(en_curso) == "in_progress"
    assert situacion(por_revisar) == "awaiting_review"
    assert situacion(descartado) == "discarded"
    assert situacion(publicado) == "published"
    # sin revisión nadie espera tu criterio, pero lo que ya decidiste sigue
    # decidido: descartado sigue descartado y publicado sigue publicado
    assert situacion(por_revisar, con_revision=False) == "in_progress"
    assert situacion(descartado, con_revision=False) == "discarded"
    assert situacion(publicado, con_revision=False) == "published"


def test_despublicar_devuelve_el_clip_a_la_situacion_de_por_revisar():
    """La situación acompaña a la transición: deja de estar publicado."""
    db, video_id = _db()
    clip_id = db.add_clip(video_id, 0, _clip())
    db.clip_rendered(clip_id, "clip.mp4")
    db.clip_approved(clip_id, "Título", "Descripción", [])
    db.clip_uploaded(clip_id, "yt-123", "2026-07-23T18:00:00+00:00")
    assert db.clip_situation(db.clips_for_video(video_id)[0], True) == "published"

    db.clip_unpublished(clip_id)
    assert db.clip_situation(db.clips_for_video(video_id)[0], True) == "awaiting_review"


# --- qué le falta a una grabación ----------------------------------------

def test_los_predicados_de_la_grabacion_dicen_que_trabajo_le_queda():
    """Transcribir y seleccionar se preguntan por separado, y en ese orden."""
    db = State(":memory:")
    video_id = db.add_video("local", "grabacion.mp4")

    row = db.recent_videos()[0]
    assert db.needs_transcription(row) and db.needs_selection(row)

    db.video_transcribed(video_id)
    row = db.recent_videos()[0]
    # ya transcrita pero todavía sin clips: el pipeline sigue en la misma pasada
    assert not db.needs_transcription(row)
    assert db.needs_selection(row)

    db.video_selected(video_id)
    row = db.recent_videos()[0]
    assert not db.needs_transcription(row) and not db.needs_selection(row)


# --- reencolado ----------------------------------------------------------

def test_requeue_devuelve_la_grabacion_tan_atras_como_haga_falta():
    """La que perdió su transcripción vuelve al principio; la que la conserva, no."""
    db = State(":memory:")
    fresh = db.add_video("local", "recien-llegada.mp4")     # referencia: sin tocar
    done = db.add_video("local", "ya-transcrita.mp4")       # referencia: transcrita
    db.video_transcribed(done)
    kept = db.add_video("local", "conserva-transcripcion.mp4")
    lost = db.add_video("local", "perdio-transcripcion.mp4")
    db.video_failed(kept, "explotó el render")
    db.video_failed(lost, "se borró la transcripción")
    assert [r["id"] for r in db.videos_to_process()] == [fresh, done]

    assert db.requeue_failed(lambda video_id: video_id == kept,
                             lambda path: True) == 2
    # los progresos se comparan contra dos grabaciones de referencia: el test
    # afirma a dónde volvió cada una sin nombrar lo que solo sabe state.py
    rows = {r["id"]: r for r in db.recent_videos()}
    assert rows[fresh]["status"] != rows[done]["status"]
    assert rows[lost]["status"] == rows[fresh]["status"]
    assert rows[kept]["status"] == rows[done]["status"]
    # y vuelven a la cola de trabajo limpias, sin el error que las sacó
    assert [r["id"] for r in db.videos_to_process()] == [fresh, done, kept, lost]
    assert rows[kept]["error"] is None and rows[lost]["error"] is None


def test_requeue_devuelve_el_clip_hasta_donde_le_falte_el_archivo():
    """El clip que conserva su render vuelve a su cola; el que lo perdió, a renderizar."""
    db, video_id = _db()
    approved = db.add_clip(video_id, 0, _clip())
    unreviewed = db.add_clip(video_id, 1, _clip())
    lost = db.add_clip(video_id, 2, _clip())
    db.clip_rendered(approved, "a.mp4")
    db.clip_approved(approved, "Título", "Descripción", [])
    db.clip_rendered(unreviewed, "b.mp4")
    db.clip_failed(approved, "falló la subida")
    db.clip_failed(unreviewed, "falló la subida")
    db.clip_failed(lost, "se borró el render")  # nunca llegó a tener archivo

    # el render de 'lost' no existe en disco; los otros dos sí
    assert db.requeue_failed(lambda _: True,
                             lambda path: path in ("a.mp4", "b.mp4")) == 3
    assert [r["id"] for r in db.clips_to_upload(require_review=True)] == [approved]
    assert [r["id"] for r in db.clips_to_review()] == [unreviewed]
    assert [r["id"] for r in db.clips_to_render(video_id)] == [lost]
    assert db.problem_clips() == []


# --- la guarda de progresos ----------------------------------------------

def test_un_progreso_desconocido_no_llega_a_la_base():
    """Un progreso fuera de la lista es un error, no una fila rara en la base."""
    db, video_id = _db()
    clip_id = db.add_clip(video_id, 0, _clip())
    # ninguna transición pública acepta un progreso arbitrario: se llama a los
    # escritores internos porque la guarda es justo lo que se está probando
    with pytest.raises(ValueError, match="progreso desconocido"):
        db._set_clip(clip_id, status="publicadísimo")
    with pytest.raises(ValueError, match="progreso desconocido"):
        db._set_video(video_id, status="medio transcrito")
    # y nada se movió de sitio
    assert [r["id"] for r in db.clips_to_render(video_id)] == [clip_id]
    assert [r["id"] for r in db.videos_to_process()] == [video_id]


def test_una_transicion_que_no_escribe_nada_es_un_error():
    """Una transición vacía sería SQL inválido: se corta antes de llegar ahí."""
    db, video_id = _db()
    clip_id = db.add_clip(video_id, 0, _clip())
    with pytest.raises(ValueError, match="transición vacía"):
        db._set_clip(clip_id)


# --- métricas y hueco de publicación -------------------------------------

def test_record_metrics_deja_las_cifras_en_la_fila():
    """Una tanda de métricas se guarda entera y dice cuántas filas tocó."""
    db, video_id = _db()
    flojo = db.add_clip(video_id, 0, _clip())
    bueno = db.add_clip(video_id, 1, _clip())

    assert db.record_metrics([(flojo, 10, 1), (bueno, 1000, 50)]) == 2
    rows = {r["id"]: r for r in db.clips_for_video(video_id)}
    assert (rows[bueno]["views"], rows[bueno]["likes"]) == (1000, 50)
    assert (rows[flojo]["views"], rows[flojo]["likes"]) == (10, 1)
    assert rows[bueno]["stats_at"]
    assert [r["id"] for r in db.clips_with_views()] == [bueno, flojo]


def test_los_conteos_de_la_corrida_no_piden_conocer_los_progresos():
    """El resumen de una corrida se pregunta en dos cifras, sin literales."""
    db, video_id = _db()
    subido = db.add_clip(video_id, 0, _clip())
    en_cola = db.add_clip(video_id, 1, _clip())
    assert (db.count_published(), db.count_queued()) == (0, 0)

    db.clip_rendered(subido, "a.mp4")
    db.clip_rendered(en_cola, "b.mp4")
    assert (db.count_published(), db.count_queued()) == (0, 2)

    db.clip_approved(subido, "Título", "Descripción", [])
    db.clip_uploaded(subido, "yt-123", None)
    assert (db.count_published(), db.count_queued()) == (1, 1)


def test_el_hueco_de_publicacion_empieza_vacio_y_se_sobreescribe():
    """El calendario recuerda un solo hueco: el último que se consumió."""
    db = State(":memory:")
    assert db.last_publish_at() is None
    db.set_last_publish_at("2026-07-22T18:00:00+00:00")
    assert db.last_publish_at() == "2026-07-22T18:00:00+00:00"
    db.set_last_publish_at("2026-07-23T18:00:00+00:00")
    assert db.last_publish_at() == "2026-07-23T18:00:00+00:00"


# --- migración -----------------------------------------------------------

# Las columnas que _migrate agrega. El esquema viejo es el de hoy sin ellas.
_ADDED_COLUMNS = {"text", "score", "views", "likes", "stats_at", "marked",
                  "approved"}


def _old_schema() -> str:
    """El DDL como era antes: el actual menos las columnas que llegaron después."""
    lines = []
    for line in SCHEMA.splitlines():
        parts = line.split()
        if parts and parts[0] in _ADDED_COLUMNS:
            continue
        lines.append(line)
    return "\n".join(lines)


def test_una_base_vieja_se_migra_sin_perder_lo_que_tenia(tmp_path: Path):
    """Abrir una base anterior agrega las columnas nuevas y respeta sus filas."""
    # el único test que toca disco: la base real del usuario nació así
    path = tmp_path / "state.db"
    old = sqlite3.connect(path)
    old.executescript(_old_schema())
    old.execute(
        "INSERT INTO videos (source, source_id, title, path, duration, created_at)"
        " VALUES ('local', 'vieja.mp4', 'grabación vieja', 'vieja.mp4', 600.0,"
        " '2024-01-01T00:00:00+00:00')"
    )
    old.execute(
        "INSERT INTO clips (video_id, idx, start, end, title, description, tags,"
        " path, created_at) VALUES (1, 0, 0.0, 30.0, 'clip viejo', 'de antes',"
        " '[\"gaming\"]', 'viejo.mp4', '2024-01-01T00:00:00+00:00')"
    )
    old.commit()
    old.close()

    db = State(path)
    rows = db.clips_for_video(1)
    assert len(rows) == 1
    assert _ADDED_COLUMNS <= set(rows[0].keys())
    assert rows[0]["title"] == "clip viejo"
    assert rows[0]["path"] == "viejo.mp4"
    assert json.loads(rows[0]["tags"]) == ["gaming"]
    # y la fila vieja sigue siendo consultable por las colas de hoy, que son
    # las que preguntan por las columnas recién agregadas
    assert [r["id"] for r in db.clips_to_render(1)] == [1]
    assert db.clips_to_review() == []
    assert db.clips_to_upload(require_review=True) == []
