"""CLI de aurclips.

Uso:
    python -m aurclips clip RUTA # recortar una grabación y ya (sin pipeline)
    python -m aurclips run       # pipeline completo (ingesta -> proceso -> subida)
    python -m aurclips watch     # modo continuo: vigila el inbox y procesa solo
    python -m aurclips mark      # marcar en vivo mientras grabas
    python -m aurclips mark RUTA # repaso: ver la grabación y marcar con Enter

    clip y mark también aceptan una URL de YouTube: el video se descarga a
    data/downloads (una sola vez) y el comando sigue igual.
    python -m aurclips review    # aprobar o corregir títulos antes de subir
    python -m aurclips ingest    # solo buscar/descargar contenido nuevo
    python -m aurclips process   # solo transcribir + seleccionar + renderizar
    python -m aurclips upload    # solo subir clips renderizados
    python -m aurclips auth      # iniciar sesión de YouTube (una sola vez)
    python -m aurclips status    # ver estado de videos y clips
    python -m aurclips doctor    # salud: dependencias, última corrida, disco
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


def cmd_ingest(cfg: Config, db: State) -> int:
    from .ingest import ingest
    return ingest(cfg, db)


def cmd_process(cfg: Config, db: State, keep_going=None) -> tuple[int, int]:
    """Procesa lo pendiente. Devuelve (videos procesados, clips renderizados)
    de ESTA corrida — los números que el evento 'run' registra en events.log."""
    from .render import render_clip
    from .safety import screen_clip
    from .select_clips import clip_words, select_clips
    from .transcribe import transcribe

    procesados = rendered = 0
    videos = db.videos_to_process()
    if not videos:
        print("[2/4] No hay videos pendientes por procesar")
        return 0, 0
    max_videos = cfg.get("limits.max_videos_per_run", 3)
    if len(videos) > max_videos:
        print(f"[2/4] {len(videos)} pendientes; se procesan {max_videos} "
              f"(limits.max_videos_per_run)")
        videos = videos[:max_videos]
    for video in videos:
        if keep_going is not None and not keep_going():
            # apagado ordenado del demonio: se respeta la transición en curso
            # y el resto queda en cola — el estado resumible hace el resto
            print("  [proceso] parada solicitada; lo pendiente queda en cola")
            break
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
            # la guarda de clips existentes evita re-seleccionar tras una
            # corrida muerta a medias: con clips ya en la base, el dedup los
            # tacharía todos de duplicados y los pendientes quedarían
            # huérfanos con el video mintiendo 'done'; lo que toca es
            # saltar directo a renderizarlos
            if db.needs_selection(video) and not db.video_has_clips(vid):
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
                rendered += 1
            db.video_finished(vid)
            procesados += 1
        except Exception as e:  # noqa: BLE001 — un video fallido no detiene el resto
            print(f"  [error] video {vid} ({title}): {e}")
            if not isinstance(e, RuntimeError):
                # los RuntimeError del pipeline son mensajes curados de una
                # línea (ffmpeg, modelo de Whisper, sesión); la traza es para
                # lo inesperado
                traceback.print_exc()
            db.video_failed(vid, str(e))
            from .notify import notify
            notify(cfg, "error", f"Falló el video '{title}': {str(e)[:200]}")
    return procesados, rendered


def cmd_mark(cfg: Config, name: str | None = None):
    """Marcar: en vivo (nombre de sesión) o repasando (ruta de un video).

    Si el argumento es un archivo que existe, es un repaso: se abre en mpv y
    cada Enter marca el momento que está sonando. Si no, es la sesión en vivo
    de siempre. Ninguna de las dos toca la base.
    """
    from .ingest import is_url, url_download
    from .marks import record_session, review_session
    from .player import find_mpv

    if name and is_url(name):
        try:
            name = str(url_download(cfg, name))
        except (OSError, ValueError) as e:
            print(f"[error] {e}")
            sys.exit(1)
    if name and Path(name).is_file():
        try:
            find_mpv()  # se comprueba aquí: el error de "instala mpv" es solo
        except FileNotFoundError as e:  # del arranque, no de toda la sesión
            print(f"[error] {e}")
            sys.exit(1)
        review_session(name)
        return
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


def cmd_upload(cfg: Config, db: State) -> int:
    from .upload import upload_pending
    return upload_pending(cfg, db)


def cmd_auth(cfg: Config, db: State):
    from .upload import get_credentials
    get_credentials(cfg, interactive=True)
    print("Autenticación de YouTube completada.")


def cmd_doctor(cfg: Config, db: State):
    """Salud del sistema de un vistazo: dependencias, última corrida, colas, disco.

    Solo compone sondas que ya existen; nada nuevo que mantener. Lo opcional
    (mpv, Ollama, GPU, credenciales) se reporta como estado, no como falla.
    """
    import shutil as sh

    from .runner import dir_size, last_run_info

    def probe(nombre: str, fn, opcional: bool = False, arreglo: str = ""):
        try:
            valor = fn()
            extra = f" — {valor}" if isinstance(valor, str) else ""
            print(f"  OK      {nombre}{extra}")
        except Exception as e:  # noqa: BLE001 — doctor reporta, nunca revienta
            etiqueta = "opcional" if opcional else "FALTA  "
            arreglo = f" ({arreglo})" if arreglo else ""
            print(f"  {etiqueta} {nombre}: {str(e).splitlines()[0][:120]}{arreglo}")

    print("== Dependencias ==")
    probe("ffmpeg", lambda: Path(cfg.ffmpeg).name)
    probe("ffprobe", lambda: Path(cfg.ffprobe).name)
    probe("yt-dlp", lambda: __import__("yt_dlp").version.__version__)

    def _mpv():
        from .player import find_mpv
        return Path(find_mpv()).name
    probe("mpv (repaso)", _mpv, opcional=True)

    def _ollama():
        from . import titles
        if not titles.available(cfg):
            raise RuntimeError("no está corriendo; los títulos salen heurísticos")
        return cfg.get("titles.model", titles.DEFAULT_MODEL)
    probe("Ollama (títulos)", _ollama, opcional=True)

    def _gpu():
        from .transcribe import _cuda_available
        if not _cuda_available():
            raise RuntimeError("sin GPU CUDA; Whisper corre en CPU")
        return "GPU CUDA disponible"
    probe("GPU", _gpu, opcional=True)

    def _sesion():
        from .upload import get_credentials
        get_credentials(cfg, interactive=False)
        return "sesión de YouTube vigente"
    probe("YouTube", _sesion, opcional=not cfg.get("upload.enabled", False),
          arreglo="aurclips auth")

    print("\n== Última corrida ==")
    info = last_run_info(cfg.logs_dir)
    if info is None:
        print("  (ninguna todavía)")
    else:
        nombre, ok = info
        estado = "completa" if ok else f"INCOMPLETA — revisa logs/{nombre}"
        print(f"  {nombre}: {estado}")

    print("\n== Colas ==")
    print(f"  {len(db.videos_to_process())} video(s) con trabajo pendiente")
    print(f"  {len(db.clips_to_review())} clip(s) esperando tu revisión")
    print(f"  {db.count_queued()} clip(s) en cola de subida")
    problemas = db.problem_clips()
    if problemas:
        print(f"  {len(problemas)} clip(s) con problemas — mira 'aurclips report'")

    print("\n== Disco ==")
    for nombre, carpeta in (("descargas", cfg.downloads_dir),
                            ("trabajo/caché", cfg.work_dir),
                            ("salida", cfg.output_dir)):
        print(f"  {nombre}: {dir_size(carpeta) / 1e9:.1f} GB")
    libre = sh.disk_usage(cfg.data_dir).free / 1e9
    aviso = "  ⚠ queda poco espacio" if libre < 10 else ""
    print(f"  libre en disco: {libre:.0f} GB{aviso}")


def cmd_status(cfg: Config, db: State):
    from .runner import last_run_info

    info = last_run_info(cfg.logs_dir)
    if info is not None:
        nombre, ok = info
        estado = "completa" if ok else f"INCOMPLETA — revisa logs/{nombre}"
        print(f"Última corrida: {nombre} — {estado}\n")
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


def _requeue_failed(cfg: Config, db: State) -> int:
    """Reencola lo fallido comprobando qué artefactos sobreviven en disco."""
    def has_transcript(video_id: int) -> bool:
        return (cfg.work_dir / f"video_{video_id}" / "transcript.json").exists()

    def render_exists(path: str) -> bool:
        return Path(path).exists()

    return db.requeue_failed(has_transcript, render_exists)


def cmd_retry(cfg: Config, db: State):
    """Reencola videos y clips fallidos."""
    n = _requeue_failed(cfg, db)
    print(f"{n} elemento(s) reencolados. Corre 'run' para reintentarlos.")


def cmd_run(cfg: Config, db: State):
    """Corrida diaria: se protege contra solapes y deja su propio log.

    El lock reemplaza el -MultipleInstances del Task Scheduler (cron/launchd no
    lo dan); la captura y rotación del log vivían en run.ps1 y ahora son iguales
    en los tres SO, así que el scheduler solo llama a `aurclips run`.
    """
    from datetime import datetime

    from .runner import prune_run_logs, single_instance, tee_output

    logs_dir = cfg.logs_dir
    with single_instance(logs_dir / "run.lock") as acquired:
        if not acquired:
            print("Ya hay una corrida en marcha; esta se omite.")
            return
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        died: Exception | None = None
        with tee_output(logs_dir / f"run_{stamp}.log"):
            try:
                _run_pipeline(cfg, db)
            except Exception as e:  # noqa: BLE001 — una corrida muerta tiene
                # que ser VISIBLE: la traza queda en el run log (el tee sigue
                # activo aquí) y el evento en events.log/Discord; sin esto, la
                # corrida de las 3 AM moría sin dejar rastro en ningún lado
                died = e
                traceback.print_exc()
                from .notify import notify
                notify(cfg, "error", f"La corrida murió: {str(e)[:300]}")
        prune_run_logs(logs_dir, keep=30)
        if died is not None:
            sys.exit(1)


def _run_pipeline(cfg: Config, db: State):
    import time

    from .notify import notify

    # métricas DE ESTA corrida en el evento 'run': events.log se vuelve el
    # registro histórico por corrida, gratis — y con 'run' en alerts.notify_on
    # es además el latido diario en Discord (si un día falta, no corrió)
    t0 = time.monotonic()
    nuevos = cmd_ingest(cfg, db) or 0
    procesados, clips_nuevos = cmd_process(cfg, db)
    subidos = cmd_upload(cfg, db) or 0
    minutos = (time.monotonic() - t0) / 60
    uploaded = db.count_published()
    queued = db.count_queued()
    notify(cfg, "run",
           f"Corrida completa en {minutos:.0f}m: {nuevos} video(s) nuevos, "
           f"{procesados} procesados, {clips_nuevos} clips renderizados, "
           f"{subidos} subidos; acumulado {uploaded} publicados, {queued} en cola")
    print("\nCorrida completa.")
    # la corrida diaria pasa de madrugada: si algo espera tu criterio, que te
    # busque a ti — el loop no se cierra solo
    pending = len(db.clips_to_review())
    if pending and cfg.get("review.enabled", True):
        print(f"{pending} clip(s) esperan tu visto bueno: python -m aurclips review")
        notify(cfg, "review",
               f"{pending} clip(s) listos y esperando tu revisión "
               f"(python -m aurclips review)")


def _watch_cycle(cfg: Config, db: State, keep_going) -> bool:
    """Un ciclo del demonio. Devuelve True si hubo trabajo.

    Silencioso cuando no hay nada: un ciclo vacío no imprime cabeceras ni
    toca la red. Los canales y el reintento automático van en sus propias
    cadencias (tabla meta), no en cada vuelta del loop.
    """
    from datetime import datetime

    from .ingest import check_channels, scan_inbox
    from .notify import notify
    from .runner import cadence_due

    did = False
    now = datetime.now()

    # lo fallido transitorio (red caída, archivo a medias) se cura solo, con
    # cadencia acotada para que lo permanente no se reintente en bucle
    retry_hours = cfg.get("watch.retry_hours", 12)
    if retry_hours and cadence_due(db.meta_get("last_auto_retry"), retry_hours, now):
        db.meta_set("last_auto_retry", now.isoformat())
        requeued = _requeue_failed(cfg, db)
        if requeued:
            print(f"[watch] reintento automático: {requeued} elemento(s) reencolados")
            did = True

    # los canales son red: se miran cada watch.channel_minutes, no cada ciclo
    channel_minutes = cfg.get("watch.channel_minutes", 60)
    if (cfg.get("channels") or []) and cadence_due(
            db.meta_get("last_channel_check"), channel_minutes / 60, now):
        db.meta_set("last_channel_check", now.isoformat())
        if check_channels(cfg, db):
            did = True

    # el inbox es disco local: barato, cada ciclo
    if scan_inbox(cfg, db):
        did = True

    if db.videos_to_process():
        before_review = len(db.clips_to_review())
        cmd_process(cfg, db, keep_going)
        did = True
        fresh = len(db.clips_to_review()) - before_review
        if fresh > 0 and cfg.get("review.enabled", True):
            notify(cfg, "review",
                   f"{fresh} clip(s) nuevos esperando tu revisión "
                   f"(aurclips review)")

    if keep_going() and cfg.get("upload.enabled", False):
        require_review = cfg.get("review.enabled", True)
        if db.clips_to_upload(require_review=require_review):
            from .upload import upload_pending
            if upload_pending(cfg, db):
                did = True
    return did


def cmd_watch(cfg: Config, db: State):
    """Modo continuo: vigila el inbox y procesa lo que llegue, sin parar.

    El demonio del pipeline. Comparte el lock con `run` (si la corrida diaria
    está en marcha, el ciclo se salta), un ciclo fallido nunca mata el loop
    (backoff exponencial acotado + evento de error), y SIGINT/SIGTERM piden
    parada ordenada: se termina la transición en curso, se guarda, y el
    estado resumible retoma en el siguiente arranque.
    """
    import signal
    import time
    from datetime import datetime

    from .notify import notify
    from .runner import prune_run_logs, single_instance, tee_output

    poll = max(5, int(cfg.get("watch.poll_seconds", 60)))
    stopping = False

    def _request_stop(signum, frame):
        nonlocal stopping
        if not stopping:
            print("\n[watch] parada solicitada; se termina lo que está en curso...")
        stopping = True

    for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            signal.signal(sig, _request_stop)

    def keep_going() -> bool:
        return not stopping

    logs_dir = cfg.logs_dir
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    with tee_output(logs_dir / f"watch_{stamp}.log"):
        print(f"aurclips watch: vigilando {cfg.inbox_dir} "
              f"(ciclo cada {poll}s; Ctrl+C para terminar)")
        backoff = poll
        while not stopping:
            with single_instance(logs_dir / "run.lock") as acquired:
                if not acquired:
                    print("[watch] otra corrida tiene el lock; este ciclo se salta")
                else:
                    try:
                        _watch_cycle(cfg, db, keep_going)
                        backoff = poll
                    except Exception as e:  # noqa: BLE001 — un ciclo fallido
                        # jamás termina el demonio: traza al log, evento, y
                        # backoff para no martillear un fallo persistente
                        traceback.print_exc()
                        notify(cfg, "error",
                               f"Ciclo de watch falló: {str(e)[:200]}")
                        backoff = min(backoff * 2, 3600)
                        print(f"[watch] ciclo fallido; reintento en {backoff}s")
            waited = 0.0
            while not stopping and waited < backoff:
                time.sleep(0.5)
                waited += 0.5
        print("[watch] detenido; el estado queda guardado y el pipeline "
              "retoma donde quedó.")
    prune_run_logs(logs_dir, keep=10, pattern="watch_*.log")


def cmd_clip(cfg: Config, path: str | None, out: str | None,
             max_clips: int | None):
    """Modo recortador: una grabación (ruta o URL) entra, salen recortes.

    No abre la base: no hay progreso ni criterio que guardar. Por eso recibe
    la config y no el par (cfg, db). Una URL se resuelve primero a un archivo
    en downloads/ (descargando una sola vez) y el resto sigue igual.
    """
    from .clipper import clip_recording
    from .ingest import is_url, url_download

    if not path:
        print("Falta la grabación: aurclips clip RUTA_O_URL_DEL_VIDEO")
        sys.exit(2)
    if max_clips is not None and max_clips < 1:
        print("--clips es un tope: tiene que ser 1 o más")
        sys.exit(2)
    try:
        if is_url(path):
            path = str(url_download(cfg, path))
        clip_recording(cfg, path, out, max_clips)
    except (OSError, ValueError) as e:
        print(f"[error] {e}")
        sys.exit(1)


COMMANDS = {
    "run": cmd_run,
    "watch": cmd_watch,
    "doctor": cmd_doctor,
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
    # clip y mark van fuera de COMMANDS: no reciben (cfg, db) porque no tocan
    # la base, así que se despachan aparte antes de abrirla
    parser.add_argument("command", choices=["clip", "mark", *COMMANDS])
    parser.add_argument("name", nargs="?",
                        help="ruta de la grabación (para 'clip', o para 'mark' "
                             "en modo repaso) o nombre de la sesión en vivo")
    parser.add_argument("--out", metavar="CARPETA",
                        help="dónde dejar los recortes (solo 'clip')")
    parser.add_argument("--clips", type=int, metavar="N",
                        help="tope de recortes en esta corrida (solo 'clip')")
    args = parser.parse_args()
    try:
        # 'clip' y 'mark' no tocan la base: se carga solo la config
        if args.command == "clip":
            cmd_clip(Config(), args.name, args.out, args.clips)
            return
        if args.command == "mark":
            cmd_mark(Config(), args.name)
            return
        cfg, db = _load()
        COMMANDS[args.command](cfg, db)
    except KeyboardInterrupt:
        print("\nInterrumpido.")
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — la última red: ningún comando
        # muere invisible. Cubre también los fallos ANTES de abrir el log de
        # corrida (state.db bloqueada, config.yaml roto): notify tolera
        # cfg=None cayendo a logs/ bajo la raíz.
        traceback.print_exc()
        from .notify import notify
        notify(None, "error", f"aurclips {args.command} murió: {str(e)[:300]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
