"""El ciclo de vida de grabaciones y clips, en SQLite.

Este módulo es el único dueño del estado persistente: nadie fuera de aquí
escribe SQL, ni nombres de columna, ni literales de progreso. Su interfaz habla
en transiciones del dominio —``video_transcribed``, ``clip_approved``,
``clip_unpublished``— y en consultas con nombre. Lo que un llamador necesita
saber para usarlo bien es qué transición ocurrió, no qué columnas viajan juntas.

Los dos ejes del estado de un clip son independientes y no hay que confundirlos
(ver CONTEXT.md):

- **Progreso** (``clips.status``): hasta dónde llegó por la parte mecánica.
- **Criterio** (``clips.approved``): el veredicto del creador — NULL sin
  revisar, 1 aprobado, 0 descartado.

"Listo para subir" es la combinación de ambos, y se resuelve en un solo sitio:
:meth:`State.clips_to_upload`.

Publicar no es terminal: :meth:`State.clip_unpublished` devuelve a la cola un
clip cuyo Short se borró de YouTube.

El esquema está congelado a propósito: la base viva del usuario ya diverge del
DDL declarado (las columnas de ``_migrate`` quedaron al final), no hay marcador
de versión, y hay filas antiguas con ``marked`` en NULL que un CHECK o un
NOT NULL rechazarían. Por eso la validación de progresos vive en Python.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,             -- 'youtube' | 'local'
    source_id   TEXT NOT NULL UNIQUE,      -- id de YouTube o ruta del archivo
    title       TEXT,
    path        TEXT,
    duration    REAL,
    status      TEXT NOT NULL DEFAULT 'new',  -- new|transcribed|selected|done|skipped|failed
    error       TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
    id          INTEGER PRIMARY KEY,
    video_id    INTEGER NOT NULL REFERENCES videos(id),
    idx         INTEGER NOT NULL,
    start       REAL NOT NULL,
    end         REAL NOT NULL,
    title       TEXT,
    description TEXT,
    tags        TEXT,                      -- JSON
    text        TEXT,                      -- transcripción del clip (dedup/filtro)
    score       REAL,                      -- puntuación de la heurística
    marked      INTEGER,                   -- 1 = lo marcaste tú al grabar
    approved    INTEGER,                   -- NULL sin revisar | 1 aprobado | 0 descartado
    path        TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending|rendered|uploaded|flagged|failed
    publish_at  TEXT,
    youtube_id  TEXT,
    views       INTEGER,
    likes       INTEGER,
    stats_at    TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Progresos válidos. Son la fuente de verdad: el esquema no los restringe (no
# hay CHECK), así que la guarda está aquí, donde ocurren todas las escrituras.
VIDEO_STATUSES = frozenset(
    {"new", "transcribed", "selected", "done", "skipped", "failed"}
)
CLIP_STATUSES = frozenset({"pending", "rendered", "uploaded", "flagged", "failed"})

# Grabaciones que todavía tienen trabajo pendiente en el pipeline.
_PROCESSABLE = ("new", "transcribed", "selected")

_LAST_PUBLISH_KEY = "last_publish_at"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class State:
    def __init__(self, db_path: Path | str):
        # acepta ':memory:' además de una ruta: los tests del ciclo de vida
        # corren contra la implementación real, sin tocar disco
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self):
        """Agrega columnas nuevas a bases creadas con versiones anteriores."""
        existing = {r[1] for r in self._conn.execute("PRAGMA table_info(clips)")}
        for col, ddl in [("text", "TEXT"), ("score", "REAL"),
                         ("views", "INTEGER"), ("likes", "INTEGER"),
                         ("stats_at", "TEXT"), ("marked", "INTEGER"),
                         ("approved", "INTEGER")]:
            if col not in existing:
                self._conn.execute(f"ALTER TABLE clips ADD COLUMN {col} {ddl}")

    # --- escritura interna -----------------------------------------------
    # Las transiciones públicas pasan por aquí: un solo sitio donde se arma el
    # UPDATE y se valida el progreso. Una transición = un commit.

    def _update(self, table: str, row_id: int, statuses: frozenset, **fields):
        if not fields:
            raise ValueError(f"transición vacía sobre {table}: no hay nada que escribir")
        if "status" in fields and fields["status"] not in statuses:
            raise ValueError(
                f"progreso desconocido para {table}: {fields['status']!r} "
                f"(válidos: {', '.join(sorted(statuses))})"
            )
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._conn.execute(
            f"UPDATE {table} SET {cols} WHERE id = ?", (*fields.values(), row_id)
        )
        self._conn.commit()

    def _set_video(self, video_id: int, **fields):
        self._update("videos", video_id, VIDEO_STATUSES, **fields)

    def _set_clip(self, clip_id: int, **fields):
        self._update("clips", clip_id, CLIP_STATUSES, **fields)

    # --- grabaciones: alta y consultas -----------------------------------
    def video_known(self, source_id: str) -> bool:
        cur = self._conn.execute("SELECT 1 FROM videos WHERE source_id = ?", (source_id,))
        return cur.fetchone() is not None

    def add_video(self, source: str, source_id: str, title: str | None = None,
                  path: str | None = None, duration: float | None = None,
                  *, skipped: bool = False) -> int:
        """Registra una grabación. ``skipped=True`` la deja fuera del pipeline."""
        status = "skipped" if skipped else "new"
        cur = self._conn.execute(
            "INSERT INTO videos (source, source_id, title, path, duration, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (source, source_id, title, path, duration, status, _now()),
        )
        self._conn.commit()
        return cur.lastrowid

    def videos_to_process(self) -> list[sqlite3.Row]:
        """Grabaciones con trabajo pendiente, en orden de llegada."""
        marks = ", ".join("?" for _ in _PROCESSABLE)
        cur = self._conn.execute(
            f"SELECT * FROM videos WHERE status IN ({marks}) ORDER BY id", _PROCESSABLE
        )
        return cur.fetchall()

    def needs_transcription(self, video: sqlite3.Row) -> bool:
        """¿A esta grabación le falta transcribirse?"""
        return video["status"] == "new"

    def needs_selection(self, video: sqlite3.Row) -> bool:
        """¿A esta grabación le falta elegirle clips?

        Responde sobre la fila que recibe, no sobre lo que diga la base ahora
        mismo, y el pipeline depende de ello: transcribe y selecciona en la
        misma pasada, con la instantánea que pidió al empezar. Releer aquí
        dejaría de seleccionar justo después de transcribir.
        """
        return video["status"] in ("new", "transcribed")

    def video_has_clips(self, video_id: int) -> bool:
        """¿Esta grabación ya tiene clips seleccionados (en cualquier estado)?

        Es la guarda contra re-seleccionar: si una corrida murió entre elegir
        clips y marcar la grabación como 'selected', volver a seleccionar
        chocaría con el dedup (los clips propios ya están en la base), daría
        cero altas y dejaría los clips pendientes huérfanos con la grabación
        mintiendo 'done'. Con clips existentes, lo que toca es renderizarlos.
        """
        cur = self._conn.execute(
            "SELECT 1 FROM clips WHERE video_id = ? LIMIT 1", (video_id,)
        )
        return cur.fetchone() is not None

    def recent_videos(self, limit: int = 20) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM videos ORDER BY id DESC LIMIT ?", (limit,)
        )
        return cur.fetchall()

    def count_videos_by_status(self) -> list[tuple[str, int]]:
        cur = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM videos GROUP BY status ORDER BY status"
        )
        return [(r["status"], r["n"]) for r in cur.fetchall()]

    # --- grabaciones: transiciones ---------------------------------------
    def video_transcribed(self, video_id: int):
        self._set_video(video_id, status="transcribed")

    def video_selected(self, video_id: int):
        self._set_video(video_id, status="selected")

    def video_finished(self, video_id: int):
        """Terminada: sin clips útiles, todos filtrados, o renderizada entera.

        Los tres casos comparten estado: el esquema está congelado y no hay
        dónde guardar el matiz. Se distinguen por los clips que colgaron de
        ella, o por lo que quedó en el log.
        """
        self._set_video(video_id, status="done")

    def video_failed(self, video_id: int, error: str):
        self._set_video(video_id, status="failed", error=error[:500])

    # --- clips: alta y consultas -----------------------------------------
    def add_clip(self, video_id: int, idx: int, clip, text: str | None = None,
                 *, flagged: bool = False) -> int:
        """Persiste un clip recién seleccionado.

        ``clip`` es lo que devuelve el selector: cualquier objeto con ``start``,
        ``end``, ``title``, ``description``, ``tags``, ``score`` y ``marked``.
        Aquí se resuelve el paso a fila —serializar los tags, normalizar
        ``marked``— para que el llamador no conozca el esquema.

        ``text`` es la transcripción que usan el filtro de contenido y el
        deduplicador; ``flagged=True`` guarda el clip señalado en vez de listo.
        """
        cur = self._conn.execute(
            "INSERT INTO clips (video_id, idx, start, end, title, description, tags,"
            " text, score, marked, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (video_id, idx, clip.start, clip.end, clip.title, clip.description,
             json.dumps(list(clip.tags)), text, clip.score, int(bool(clip.marked)),
             "flagged" if flagged else "pending", _now()),
        )
        self._conn.commit()
        return cur.lastrowid

    def clip_tags(self, clip: sqlite3.Row) -> list[str]:
        """Los tags de un clip, ya deserializados.

        La contraparte de lo que serializan :meth:`add_clip` y
        :meth:`clip_approved`: la conversión entera vive aquí, no a medias.
        """
        return json.loads(clip["tags"] or "[]")

    def clips_for_video(self, video_id: int) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM clips WHERE video_id = ? ORDER BY idx", (video_id,)
        )
        return cur.fetchall()

    def clips_to_render(self, video_id: int) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM clips WHERE video_id = ? AND status = 'pending' ORDER BY idx",
            (video_id,),
        )
        return cur.fetchall()

    def clips_to_review(self) -> list[sqlite3.Row]:
        """Clips renderizados sobre los que todavía no diste tu criterio."""
        cur = self._conn.execute(
            "SELECT * FROM clips WHERE status = 'rendered' AND approved IS NULL"
            " ORDER BY video_id, idx"
        )
        return cur.fetchall()

    def _awaits_review(self, clip: sqlite3.Row) -> bool:
        # la misma condición que el SELECT de arriba, fila a fila; van juntas
        # a propósito, porque separarlas es como se desincronizan
        return clip["status"] == "rendered" and clip["approved"] is None

    def clip_situation(self, clip: sqlite3.Row, require_review: bool) -> str:
        """En qué punto del ciclo está un clip, combinando los dos ejes.

        Devuelve ``published``, ``discarded``, ``awaiting_review`` o
        ``in_progress``: lo mismo que resuelve :meth:`clips_to_upload`, pero de
        a una fila. Quien lo muestre no necesita saber cómo se codifica tu
        criterio ni qué progresos existen.
        """
        if clip["youtube_id"]:
            return "published"
        if clip["approved"] == 0:
            return "discarded"
        if require_review and self._awaits_review(clip):
            return "awaiting_review"
        return "in_progress"

    def clips_to_upload(self, require_review: bool) -> list[sqlite3.Row]:
        """Clips listos para YouTube: la única definición de "subible".

        Con ``require_review`` solo salen los aprobados. Sin revisión sale todo
        lo renderizado —incluido lo descartado en una revisión anterior—, que es
        exactamente lo que hace hoy el pipeline desatendido.
        """
        if require_review:
            cur = self._conn.execute(
                "SELECT * FROM clips WHERE status = 'rendered' AND approved = 1"
                " ORDER BY id"
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM clips WHERE status = 'rendered' ORDER BY id"
            )
        return cur.fetchall()

    def uploaded_with_youtube_id(self) -> list[sqlite3.Row]:
        """Shorts publicados de los que se pueden pedir métricas."""
        cur = self._conn.execute(
            "SELECT id, youtube_id FROM clips "
            "WHERE status = 'uploaded' AND youtube_id IS NOT NULL AND youtube_id != ''"
        )
        return cur.fetchall()

    def published(self) -> list[sqlite3.Row]:
        """Todos los Shorts publicados. Quien reporta agrupa y promedia."""
        cur = self._conn.execute("SELECT * FROM clips WHERE status = 'uploaded'")
        return cur.fetchall()

    def clips_with_views(self) -> list[sqlite3.Row]:
        """Clips con vistas registradas, de más a menos (cualquier progreso)."""
        cur = self._conn.execute(
            "SELECT * FROM clips WHERE views IS NOT NULL ORDER BY views DESC, id ASC"
        )
        return cur.fetchall()

    def problem_clips(self) -> list[sqlite3.Row]:
        """Clips fallidos o señalados por el filtro de contenido."""
        cur = self._conn.execute(
            "SELECT * FROM clips WHERE status IN ('failed', 'flagged') ORDER BY id"
        )
        return cur.fetchall()

    def texts_for_dedup(self) -> list[sqlite3.Row]:
        """(id, text) de todo clip con transcripción, para buscar duplicados."""
        cur = self._conn.execute(
            "SELECT id, text FROM clips WHERE text IS NOT NULL AND text != ''"
        )
        return cur.fetchall()

    def recent_clips(self, limit: int = 20) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM clips ORDER BY id DESC LIMIT ?", (limit,)
        )
        return cur.fetchall()

    def count_clips_by_status(self) -> list[tuple[str, int]]:
        cur = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM clips GROUP BY status ORDER BY status"
        )
        return [(r["status"], r["n"]) for r in cur.fetchall()]

    def count_published(self) -> int:
        """Cuántos Shorts hay ya en YouTube."""
        cur = self._conn.execute(
            "SELECT COUNT(*) AS n FROM clips WHERE status = 'uploaded'"
        )
        return cur.fetchone()["n"]

    def count_queued(self) -> int:
        """Cuántos clips están renderizados esperando su turno."""
        cur = self._conn.execute(
            "SELECT COUNT(*) AS n FROM clips WHERE status = 'rendered'"
        )
        return cur.fetchone()["n"]

    # --- clips: transiciones ---------------------------------------------
    def clip_rendered(self, clip_id: int, path: str):
        self._set_clip(clip_id, status="rendered", path=path)

    def clip_approved(self, clip_id: int, title: str, description: str,
                      tags: Iterable[str]):
        """Tu visto bueno, con las correcciones que hayas hecho en la revisión."""
        self._set_clip(clip_id, approved=1, title=title, description=description,
                       tags=json.dumps(list(tags)))

    def clip_discarded(self, clip_id: int):
        self._set_clip(clip_id, approved=0)

    def clip_uploaded(self, clip_id: int, youtube_id: str, publish_at: str | None):
        """El Short ya existe en YouTube: clip y hueco consumido, juntos.

        Una sola transacción a propósito — subir es el único paso no
        reversible del pipeline, y esta transición corre inmediatamente
        después de recibir el id de YouTube. Si el proceso muere justo aquí,
        o quedó todo escrito o nada: nunca un Short real sin registrar (que la
        corrida siguiente re-subiría, duplicando en el canal).
        """
        try:
            self._conn.execute(
                "UPDATE clips SET status = 'uploaded', youtube_id = ?,"
                " publish_at = ? WHERE id = ?",
                (youtube_id, publish_at, clip_id),
            )
            if publish_at:
                self._conn.execute(
                    "INSERT INTO meta (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (_LAST_PUBLISH_KEY, publish_at),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def clip_failed(self, clip_id: int, error: str):
        self._set_clip(clip_id, status="failed", error=error[:500])

    def clip_unpublished(self, clip_id: int):
        """Borraste el Short de YouTube: el clip vuelve a la cola de revisión.

        Limpia el rastro de la publicación y reabre el criterio, para que el
        clip aparezca otra vez en ``aurclips review`` en vez de resubirse solo.
        """
        self._set_clip(clip_id, status="rendered", youtube_id=None,
                       publish_at=None, approved=None, error=None)

    def record_metrics(self, metrics: Iterable[tuple[int, int, int]]) -> int:
        """Guarda (clip_id, views, likes) de una tanda. Todo o nada."""
        stamp = _now()
        updated = 0
        try:
            for clip_id, views, likes in metrics:
                self._conn.execute(
                    "UPDATE clips SET views = ?, likes = ?, stats_at = ? WHERE id = ?",
                    (views, likes, stamp, clip_id),
                )
                updated += 1
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return updated

    # --- reencolado -------------------------------------------------------
    def requeue_failed(self, has_transcript: Callable[[int], bool],
                       render_exists: Callable[[str], bool]) -> int:
        """Devuelve a la cola lo que falló, tan atrás como haga falta.

        Los dos callables responden por el disco —el módulo no conoce rutas—:
        ``has_transcript(video_id)`` dice si la transcripción sigue ahí, y
        ``render_exists(path)`` si el mp4 del clip existe de verdad. Comprobar
        el archivo y no solo la columna evita el bucle failed→rendered→failed
        de un clip cuyo mp4 se borró del output pero conservó la ruta escrita.

        Una grabación que ya tiene clips vuelve a 'selected', no a
        'transcribed': re-seleccionar con clips existentes es el camino de los
        huérfanos (ver video_has_clips).
        """
        n = 0
        for video in self._conn.execute(
                "SELECT id FROM videos WHERE status = 'failed' ORDER BY id").fetchall():
            if self.video_has_clips(video["id"]):
                status = "selected"
            elif has_transcript(video["id"]):
                status = "transcribed"
            else:
                status = "new"
            self._set_video(video["id"], status=status, error=None)
            n += 1
        for clip in self._conn.execute(
                "SELECT id, path FROM clips WHERE status = 'failed' ORDER BY id").fetchall():
            status = ("rendered" if clip["path"] and render_exists(clip["path"])
                      else "pending")
            self._set_clip(clip["id"], status=status, error=None)
            n += 1
        return n

    # --- hueco de publicación ---------------------------------------------
    def last_publish_at(self) -> str | None:
        """El último hueco del calendario que se consumió, si hubo alguno."""
        cur = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (_LAST_PUBLISH_KEY,)
        )
        row = cur.fetchone()
        return row["value"] if row else None

    def set_last_publish_at(self, value: str):
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_LAST_PUBLISH_KEY, value),
        )
        self._conn.commit()
