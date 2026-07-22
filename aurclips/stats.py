"""Métricas de rendimiento de los Shorts publicados.

Consulta vistas/likes en la YouTube Data API, calcula qué contenido fuente
rinde mejor (para priorizar futuras subidas) y arma el reporte de monitoreo
del comando ``report``. Solo depende de la stdlib y de googleapiclient, que ya
usa el módulo de subida.
"""

from __future__ import annotations

from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value):
    """Convierte una fecha ISO en datetime con zona; None si no se puede."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fetch_stats(cfg, db) -> int:
    """Actualiza views/likes de los clips con status='uploaded' y youtube_id.

    - Usa ``get_credentials(cfg, interactive=False)`` de ``.upload`` y construye
      el cliente youtube v3. Llama ``videos.list(part="statistics", id=...)``
      con hasta 50 ids por llamada, separados por coma.
    - Actualiza ``clips.views``, ``clips.likes`` y ``clips.stats_at`` (UTC ISO).
    - Si la API devuelve 403 por scopes insuficientes: avisa que hay que correr
      de nuevo ``python -m aurclips auth`` (el token viejo no tiene el scope de
      lectura) y devuelve 0. Cualquier otra excepción: aviso y 0.
    - Devuelve cuántos clips actualizó."""
    from .upload import get_credentials

    rows = db.conn.execute(
        "SELECT id, youtube_id FROM clips "
        "WHERE status = 'uploaded' AND youtube_id IS NOT NULL AND youtube_id != ''"
    ).fetchall()
    if not rows:
        print("  [stats] no hay clips subidos con id de YouTube")
        return 0

    # --- credenciales (sin abrir navegador) -----------------------------
    try:
        creds = get_credentials(cfg, interactive=False)
    except RuntimeError as e:
        # No hay sesión guardada todavía.
        print(f"  [aviso] {e}")
        return 0
    except Exception as e:  # noqa: BLE001 — nunca romper por credenciales
        print(f"  [aviso] no se pudieron cargar las credenciales de YouTube: {e}")
        return 0

    # --- cliente de la API ----------------------------------------------
    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        youtube = build("youtube", "v3", credentials=creds)
    except Exception as e:  # noqa: BLE001
        print(f"  [aviso] no se pudo crear el cliente de YouTube: {e}")
        return 0

    # Un mismo youtube_id apunta a un solo clip; conservamos el mapeo.
    by_yt: dict[str, int] = {}
    for r in rows:
        by_yt.setdefault(r["youtube_id"], r["id"])
    ids = list(by_yt.keys())

    stamp = _now_iso()
    updated = 0
    try:
        for i in range(0, len(ids), 50):
            batch = ids[i:i + 50]
            resp = youtube.videos().list(
                part="statistics", id=",".join(batch)
            ).execute()
            for item in resp.get("items", []):
                clip_id = by_yt.get(item.get("id"))
                if clip_id is None:
                    continue
                st = item.get("statistics", {})
                views = int(st.get("viewCount", 0) or 0)
                likes = int(st.get("likeCount", 0) or 0)
                db.conn.execute(
                    "UPDATE clips SET views = ?, likes = ?, stats_at = ? WHERE id = ?",
                    (views, likes, stamp, clip_id),
                )
                updated += 1
        db.conn.commit()
    except HttpError as e:
        status = getattr(getattr(e, "resp", None), "status", None)
        if status is None:
            status = getattr(e, "status_code", None)
        db.conn.rollback()
        if status == 403:
            print("  [aviso] YouTube devolvió 403 (permisos insuficientes).")
            print("          El token actual no tiene el scope de lectura de estadísticas.")
            print("          Ejecuta de nuevo: python -m aurclips auth")
            return 0
        print(f"  [aviso] error de la API de YouTube: {e}")
        return 0
    except Exception as e:  # noqa: BLE001
        db.conn.rollback()
        print(f"  [aviso] no se pudieron obtener estadísticas: {e}")
        return 0

    print(f"  [stats] {updated} clip(s) actualizados")
    return updated


def source_performance(db) -> dict[int, float]:
    """Promedio de views por video_id entre sus clips subidos.

    Sirve para saber qué contenido fuente rinde mejor. Los videos sin datos de
    vistas no aparecen en el resultado."""
    cur = db.conn.execute(
        "SELECT video_id, AVG(views) AS avg_views FROM clips "
        "WHERE status = 'uploaded' AND views IS NOT NULL "
        "GROUP BY video_id"
    )
    return {row["video_id"]: float(row["avg_views"]) for row in cur.fetchall()}


def order_pending(db, clips: list) -> list:
    """Ordena clips renderizados para subir primero los más prometedores.

    Recibe filas ``sqlite3.Row`` con status='rendered'. Criterios, de mayor a
    menor prioridad:
      1º rendimiento histórico del video fuente (``source_performance``, desc)
      2º ``clips.score`` (desc; None se trata como 0)."""
    perf = source_performance(db)

    def sort_key(clip):
        source_avg = perf.get(clip["video_id"], 0.0)
        score = clip["score"] if clip["score"] is not None else 0.0
        return (source_avg, score)

    return sorted(clips, key=sort_key, reverse=True)


def _num(value: float) -> str:
    return f"{value:,.0f}".replace(",", ".")


def _avg_by(rows, label_of) -> list[tuple[str, int, float]]:
    """(etiqueta, nº de shorts, vistas medias) por grupo, de mejor a peor."""
    groups: dict[str, list[int]] = {}
    for row in rows:
        groups.setdefault(label_of(row), []).append(row["views"] or 0)
    out = [(label, len(v), sum(v) / len(v)) for label, v in groups.items() if v]
    return sorted(out, key=lambda g: g[2], reverse=True)


def _duration_bucket(row) -> str:
    dur = (row["end"] or 0) - (row["start"] or 0)
    if dur <= 20:
        return "hasta 20 s"
    if dur <= 35:
        return "21-35 s"
    if dur <= 50:
        return "36-50 s"
    return "más de 50 s"


def _hook_kind(row) -> str:
    from .heuristics import HOOK_WORDS

    title = (row["title"] or "").lower()
    if "?" in title or "¿" in title:
        return "pregunta"
    if any(w in title for w in HOOK_WORDS):
        return "palabra gancho"
    return "afirmación directa"


MIN_SAMPLE = 6  # por debajo de esto los promedios son ruido, no señal


def learnings(db) -> list[str]:
    """Qué tienen en común los Shorts que rindieron: la afinación de verdad.

    No se tunea por intuición. Esto compara lo ya publicado por duración, tipo
    de gancho y origen (marcado por ti o elegido por el bot) para que ajustes
    hacia donde apunten los datos, no hacia donde apunte la corazonada.
    """
    rows = db.conn.execute(
        "SELECT title, start, end, marked, views, likes FROM clips "
        "WHERE status = 'uploaded' AND views IS NOT NULL"
    ).fetchall()
    lines = ["", "Qué está funcionando:"]
    if len(rows) < 3:
        lines.append(f"  ({len(rows)} Short(s) con métricas; hacen falta unos "
                     f"{MIN_SAMPLE} publicados para que esto diga algo)")
        return lines

    lines[-1] = f"Qué está funcionando ({len(rows)} Shorts con métricas):"
    blocks = [
        ("Por duración del clip", _duration_bucket),
        ("Por gancho del título", _hook_kind),
        ("Por origen", lambda r: "marcado por ti" if r["marked"] else "elegido por el bot"),
    ]
    for heading, label_of in blocks:
        groups = _avg_by(rows, label_of)
        if len(groups) < 2:
            continue  # un solo grupo no compara nada
        lines.append(f"  {heading}:")
        for label, n, avg in groups:
            lines.append(f"    {label:<20} {n:>3} shorts   "
                         f"{_num(avg):>8} vistas de media")
    if len(rows) < MIN_SAMPLE:
        lines.append("  (muestra pequeña: léelo como una pista, no como veredicto)")
    return lines


def review_header(db) -> list[str]:
    """Dos o tres líneas de "qué rinde", para la cabecera de ``review``.

    En ``review`` estás decidiendo, no analizando: va solo el ganador de cada
    dimensión, una línea cada uno. Y por debajo de :data:`MIN_SAMPLE` no se
    muestra **ninguna** comparación: un promedio con n=3 sesga la decisión
    justo en el momento en que más pesa ("los de 25 s rinden mejor" y empiezas
    a descartar clips largos sin base).
    """
    rows = db.conn.execute(
        "SELECT title, start, end, marked, views FROM clips "
        "WHERE status = 'uploaded' AND views IS NOT NULL"
    ).fetchall()
    if len(rows) < MIN_SAMPLE:
        return [f"(aún sin datos para guiarte: {len(rows)} Short(s) publicados "
                f"de ~{MIN_SAMPLE}; decide con tu criterio)"]

    lines = [f"Lo que mejor rinde en tus {len(rows)} Shorts publicados:"]
    dimensions = [
        ("duración", _duration_bucket),
        ("gancho", _hook_kind),
        ("origen", lambda r: "marcados por ti" if r["marked"] else "elegidos por el bot"),
    ]
    for label, label_of in dimensions:
        groups = _avg_by(rows, label_of)
        if len(groups) < 2:
            continue  # un solo grupo no compara nada
        top, n, avg = groups[0]
        lines.append(f"  {label:<9} {top} ({n} shorts, "
                     f"{_num(avg)} vistas de media)")
    return lines


def build_report(db) -> str:
    """Reporte de monitoreo en texto (español) para el comando ``report``.

    Incluye: resumen de videos y clips por estado, próximas publicaciones
    programadas, top 5 de clips por vistas y clips con problemas (fallidos o
    marcados). Solo usa la stdlib."""
    lines: list[str] = []
    sep = "=" * 52
    lines.append(sep)
    lines.append("  REPORTE DE MONITOREO — aurclips")
    lines.append(f"  {_now_iso()}")
    lines.append(sep)

    # --- resumen por estado ---------------------------------------------
    lines.append("")
    lines.append("Videos por estado:")
    vrows = db.conn.execute(
        "SELECT status, COUNT(*) AS n FROM videos GROUP BY status ORDER BY status"
    ).fetchall()
    if vrows:
        for r in vrows:
            lines.append(f"  {r['status']:<14} {r['n']}")
    else:
        lines.append("  (ninguno)")

    lines.append("")
    lines.append("Clips por estado:")
    crows = db.conn.execute(
        "SELECT status, COUNT(*) AS n FROM clips GROUP BY status ORDER BY status"
    ).fetchall()
    if crows:
        for r in crows:
            lines.append(f"  {r['status']:<14} {r['n']}")
    else:
        lines.append("  (ninguno)")

    to_review = db.conn.execute(
        "SELECT COUNT(*) AS n FROM clips WHERE status = 'rendered' AND approved IS NULL"
    ).fetchone()["n"]
    if to_review:
        lines.append(f"  {'por revisar':<14} {to_review}  (python -m aurclips review)")

    # --- próximas publicaciones programadas -----------------------------
    lines.append("")
    lines.append("Próximas publicaciones programadas:")
    now = datetime.now(timezone.utc)
    sched = db.conn.execute(
        "SELECT title, publish_at FROM clips "
        "WHERE status = 'uploaded' AND publish_at IS NOT NULL"
    ).fetchall()
    upcoming = []
    for r in sched:
        dt = _parse_iso(r["publish_at"])
        if dt is not None and dt > now:
            upcoming.append((dt, r["title"]))
    upcoming.sort(key=lambda x: x[0])
    if upcoming:
        for dt, title in upcoming:
            when = dt.strftime("%Y-%m-%d %H:%M %Z").strip()
            lines.append(f"  {when}  {title or '(sin título)'}")
    else:
        lines.append("  (ninguna)")

    # --- top 5 por vistas -----------------------------------------------
    lines.append("")
    lines.append("Top 5 clips por vistas:")
    top = db.conn.execute(
        "SELECT title, views, likes FROM clips "
        "WHERE views IS NOT NULL ORDER BY views DESC, id ASC LIMIT 5"
    ).fetchall()
    if top:
        for r in top:
            likes = r["likes"] if r["likes"] is not None else 0
            lines.append(
                f"  {r['views']:>8} vistas  {likes:>7} likes  "
                f"{r['title'] or '(sin título)'}"
            )
    else:
        lines.append("  (sin datos de vistas todavía)")

    # --- qué está funcionando (afinación por datos) ----------------------
    lines += learnings(db)

    # --- clips con problemas --------------------------------------------
    problems = db.conn.execute(
        "SELECT id, status, title, error FROM clips "
        "WHERE status IN ('failed', 'flagged') ORDER BY id"
    ).fetchall()
    if problems:
        lines.append("")
        lines.append("Clips con problemas (fallidos / marcados):")
        for r in problems:
            extra = f" — {r['error']}" if r["error"] else ""
            lines.append(
                f"  #{r['id']:<4} {r['status']:<8} {r['title'] or ''}{extra}"
            )

    lines.append("")
    return "\n".join(lines)
