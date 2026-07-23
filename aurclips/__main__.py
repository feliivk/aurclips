"""CLI de aurclips.

Uso:
    python -m aurclips clip RUTA # recortar una grabación y ya (sin pipeline)
    python -m aurclips run       # pipeline completo (ingesta -> proceso -> subida)
    python -m aurclips mark      # marcar momentos en vivo mientras grabas
    python -m aurclips review    # aprobar o corregir títulos antes de subir
    python -m aurclips ingest    # solo buscar/descargar contenido nuevo
    python -m aurclips process   # solo transcribir + seleccionar + renderizar
    python -m aurclips upload    # solo subir clips renderizados
    python -m aurclips auth      # iniciar sesión de YouTube (una sola vez)
    python -m aurclips status    # ver estado de videos y clips
    python -m aurclips report    # métricas de los Shorts publicados y cola
    python -m aurclips retry     # reencolar videos/clips fallidos
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from .config import Config
from .state import State


def _load() -> tuple[Config, State]:
    cfg = Config()
    db = State(cfg.db_path)
    return cfg, db


def cmd_ingest(cfg: Config, db: State):
    from .ingest import ingest
    ingest(cfg, db)


def cmd_process(cfg: Config, db: State):
    from .render import render_clip
    from .safety import screen_clip
    from .select_clips import clip_words, select_clips
    from .transcribe import transcribe

    videos = db.videos_to_process()
    if not videos:
        print("[2/4] No hay videos pendientes por procesar")
        return
    max_videos = cfg.get("limits.max_videos_per_run", 3)
    if len(videos) > max_videos:
        print(f"[2/4] {len(videos)} pendientes; se procesan {max_videos} "
              f"(limits.max_videos_per_run)")
        videos = videos[:max_videos]
    for video in videos:
        vid = video["id"]
        title = video["title"] or f"video_{vid}"
        workdir = cfg.work_dir / f"video_{vid}"
        transcript_path = workdir / "transcript.json"
        try:
            # --- transcribir -------------------------------------------
            if db.needs_transcription(video):
                print(f"[2/4] Transcribiendo: {title}")
                transcript = transcribe(cfg, video["path"], transcript_path)
                db.video_transcribed(vid)
            else:
                transcript = json.loads(transcript_path.read_text(encoding="utf-8"))

            # --- seleccionar clips -------------------------------------
            if db.needs_selection(video):
                print(f"[3/4] Seleccionando clips: {title}")
                clips = select_clips(cfg, transcript, title, video["path"])
                if not clips:
                    print("  sin clips útiles; video marcado como terminado")
                    db.video_finished(vid)
                    continue
                added = 0
                for i, c in enumerate(clips):
                    text = " ".join(
                        w["word"] for w in clip_words(transcript, c.start, c.end)
                    )
                    known = ((row["id"], row["text"])
                             for row in db.texts_for_dedup())
                    verdict = screen_clip(cfg, text, known)
                    if verdict.unsafe_terms:
                        print(f"  [filtro] clip {c.title!r} contiene: "
                              f"{', '.join(verdict.unsafe_terms[:5])} -> "
                              f"{cfg.get('safety.action', 'skip')}")
                    if verdict.duplicate_of is not None:
                        print(f"  [dedup] clip {c.title!r} es casi idéntico "
                              f"al clip #{verdict.duplicate_of}; se omite")
                    if not verdict.keep:
                        continue
                    db.add_clip(vid, i, c, text, flagged=verdict.flagged)
                    added += 1
                if not added:
                    print("  todos los clips fueron filtrados; video terminado")
                    db.video_finished(vid)
                    continue
                db.video_selected(vid)

            # --- renderizar --------------------------------------------
            for clip in db.clips_to_render(vid):
                words = clip_words(transcript, clip["start"], clip["end"])
                out = render_clip(cfg, video["path"], clip["start"], clip["end"],
                                  clip["title"], words, clip["id"])
                db.clip_rendered(clip["id"], str(out))
            db.video_finished(vid)
        except Exception as e:  # noqa: BLE001 — un video fallido no detiene el resto
            print(f"  [error] video {vid} ({title}): {e}")
            traceback.print_exc()
            db.video_failed(vid, str(e))
            from .notify import notify
            notify(cfg, "error", f"Falló el video '{title}': {str(e)[:200]}")


def cmd_mark(cfg: Config, db: State, name: str | None = None):
    """Sesión de marcado en vivo: cada Enter marca el instante actual."""
    from .marks import record_session
    record_session(cfg, name)


def _show_clip(clip, tags: list[str]):
    dur = clip["end"] - clip["start"]
    star = " ★ marcado por ti" if clip["marked"] else ""
    print(f"\n── clip #{clip['id']} · {clip['start']:.0f}s-{clip['end']:.0f}s "
          f"({dur:.0f}s){star}")
    print(f"   archivo: {clip['path']}")
    print(f"   título:  {clip['title']}")
    if clip["description"]:
        print(f"   desc:    {clip['description'][:160]}")
    if tags:
        print(f"   tags:    {' '.join('#' + t for t in tags)}")


def cmd_review(cfg: Config, db: State):
    """Revisa y aprueba la metadata antes de que los clips se suban.

    Son unos pocos al día: la herramienta propone y tú apruebas. Es el punto
    donde tu criterio entra al pipeline sin tener que tocar código.
    """
    from . import titles
    from .stats import review_header

    clips = db.clips_to_review()
    if not clips:
        print("No hay clips esperando revisión.")
        return
    llm = titles.enabled(cfg)
    for line in review_header(db):  # los datos, donde se toman las decisiones
        print(line)
    print()
    print(f"{len(clips)} clip(s) por revisar.")
    print("[Enter] aprobar · [t] título · [d] descripción · "
          f"{'[r] regenerar · ' if llm else ''}[x] descartar · [s] saltar · [q] salir")

    approved = discarded = 0
    for clip in clips:
        tags = db.clip_tags(clip)
        title, description = clip["title"], clip["description"]
        _show_clip(clip, tags)
        while True:
            try:
                choice = input("   > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nRevisión interrumpida; el resto queda pendiente.")
                return
            if choice in ("", "a", "ok"):
                db.clip_approved(clip["id"], title, description, tags)
                approved += 1
                break
            if choice == "t":
                new = input("     título: ").strip()
                if new:
                    title = new[:93]
                    print(f"   título:  {title}")
                continue
            if choice == "d":
                new = input("     descripción: ").strip()
                if new:
                    description = new
                continue
            if choice == "r" and llm:
                proposal = titles.propose(cfg, clip["text"] or description, "")
                if proposal:  # se guarda al aprobar, no antes
                    title, description, tags = proposal
                    print(f"   título:  {title}")
                    print(f"   desc:    {description[:160]}")
                continue
            if choice == "x":
                db.clip_discarded(clip["id"])
                discarded += 1
                print("   descartado (no se subirá)")
                break
            if choice == "s":
                break
            if choice == "q":
                print(f"\n{approved} aprobado(s), {discarded} descartado(s).")
                return
            print("   opciones: Enter / t / d / r / x / s / q")

    print(f"\n{approved} aprobado(s), {discarded} descartado(s).")


def cmd_upload(cfg: Config, db: State):
    from .upload import upload_pending
    upload_pending(cfg, db)


def cmd_auth(cfg: Config, db: State):
    from .upload import get_credentials
    get_credentials(cfg, interactive=True)
    print("Autenticación de YouTube completada.")


def cmd_status(cfg: Config, db: State):
    print("== Videos ==")
    rows = db.recent_videos(20)
    if not rows:
        print("  (ninguno)")
    for r in rows:
        dur = f"{r['duration']:.0f}s" if r["duration"] else "?"
        print(f"  #{r['id']:<4} {r['status']:<12} [{r['source']}] {r['title'] or ''} ({dur})")
    print("\n== Clips ==")
    rows = db.recent_clips(20)
    if not rows:
        print("  (ninguno)")
    review_on = cfg.get("review.enabled", True)
    for r in rows:
        extra = ""
        situation = db.clip_situation(r, review_on)
        if situation == "published":
            extra = f" -> https://youtu.be/{r['youtube_id']} @ {r['publish_at'] or '?'}"
        elif situation == "discarded":
            extra = " (descartado en revisión)"
        elif situation == "awaiting_review":
            extra = " (por revisar)"
        star = "★" if r["marked"] else " "
        print(f"  #{r['id']:<4} {star} {r['status']:<10} {r['title'] or ''}{extra}")


def cmd_report(cfg: Config, db: State):
    from .stats import build_report, fetch_stats
    if cfg.get("stats.enabled", True):
        n = fetch_stats(cfg, db)
        if n:
            print(f"(métricas actualizadas para {n} clip(s))\n")
    print(build_report(db))


def cmd_retry(cfg: Config, db: State):
    """Reencola videos y clips fallidos."""
    def has_transcript(video_id: int) -> bool:
        return (cfg.work_dir / f"video_{video_id}" / "transcript.json").exists()

    n = db.requeue_failed(has_transcript)
    print(f"{n} elemento(s) reencolados. Corre 'run' para reintentarlos.")


def cmd_run(cfg: Config, db: State):
    from .notify import notify
    cmd_ingest(cfg, db)
    cmd_process(cfg, db)
    cmd_upload(cfg, db)
    uploaded = db.count_published()
    queued = db.count_queued()
    notify(cfg, "run",
           f"Corrida completa: {uploaded} clips subidos en total, {queued} en cola")
    print("\nCorrida completa.")
    # la corrida diaria pasa de madrugada: si algo espera tu criterio, que te
    # busque a ti — el loop no se cierra solo
    pending = len(db.clips_to_review())
    if pending and cfg.get("review.enabled", True):
        print(f"{pending} clip(s) esperan tu visto bueno: python -m aurclips review")
        notify(cfg, "review",
               f"{pending} clip(s) listos y esperando tu revisión "
               f"(python -m aurclips review)")


def cmd_clip(cfg: Config, path: str | None, out: str | None,
             max_clips: int | None):
    """Modo recortador: una grabación entra, salen recortes sueltos.

    El único comando que no abre la base: no hay progreso ni criterio que
    guardar. Por eso recibe la config y no el par (cfg, db).
    """
    from .clipper import clip_recording

    if not path:
        print("Falta la grabación: python -m aurclips clip RUTA_DEL_VIDEO")
        sys.exit(2)
    if max_clips is not None and max_clips < 1:
        print("--clips es un tope: tiene que ser 1 o más")
        sys.exit(2)
    try:
        clip_recording(cfg, path, out, max_clips)
    except (OSError, ValueError) as e:
        print(f"[error] {e}")
        sys.exit(1)


COMMANDS = {
    "run": cmd_run,
    "mark": cmd_mark,
    "review": cmd_review,
    "ingest": cmd_ingest,
    "process": cmd_process,
    "upload": cmd_upload,
    "auth": cmd_auth,
    "status": cmd_status,
    "report": cmd_report,
    "retry": cmd_retry,
}


def main():
    parser = argparse.ArgumentParser(prog="aurclips", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["clip", *COMMANDS])
    parser.add_argument("name", nargs="?",
                        help="ruta de la grabación (para 'clip') o nombre de "
                             "la sesión (para 'mark')")
    parser.add_argument("--out", metavar="CARPETA",
                        help="dónde dejar los recortes (solo 'clip')")
    parser.add_argument("--clips", type=int, metavar="N",
                        help="tope de recortes en esta corrida (solo 'clip')")
    args = parser.parse_args()
    try:
        # 'clip' no toca la base: se carga solo la config
        if args.command == "clip":
            cmd_clip(Config(), args.name, args.out, args.clips)
            return
        cfg, db = _load()
        if args.command == "mark":
            cmd_mark(cfg, db, args.name)
        else:
            COMMANDS[args.command](cfg, db)
    except KeyboardInterrupt:
        print("\nInterrumpido.")
        sys.exit(130)


if __name__ == "__main__":
    main()
