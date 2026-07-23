"""Subida a YouTube con publicación programada diaria (YouTube Data API v3)."""

from __future__ import annotations

from datetime import datetime, timedelta, time as dtime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Config
from .state import State

# upload = subir videos; readonly = leer métricas (comando `report`)
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def _tz(cfg: Config):
    name = cfg.get("upload.timezone")
    if name:
        return ZoneInfo(name)
    return datetime.now().astimezone().tzinfo


def get_credentials(cfg: Config, interactive: bool = True):
    """Carga (o crea con OAuth) las credenciales de YouTube."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path = cfg.credentials_dir / "youtube_token.json"
    secrets_path = cfg.credentials_dir / "client_secrets.json"

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
    if not creds or not creds.valid:
        if not interactive:
            raise RuntimeError(
                "No hay sesión de YouTube. Ejecuta primero: python -m aurclips auth"
            )
        if not secrets_path.exists():
            raise RuntimeError(
                f"Falta {secrets_path}. Descárgalo de Google Cloud Console "
                "(credencial OAuth de tipo 'Desktop app') — ver "
                "docs/upload-youtube.md."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
        creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        print("  [youtube] sesión guardada")
    return creds


def _next_publish_slot(cfg: Config, db: State) -> datetime:
    """Siguiente hueco libre en el calendario de publicación (uno por día)."""
    tz = _tz(cfg)
    hh, mm = (cfg.get("upload.publish_time", "19:00")).split(":")
    publish_t = dtime(int(hh), int(mm))
    lead = timedelta(hours=cfg.get("upload.min_lead_hours", 1))

    now = datetime.now(tz)
    candidate = datetime.combine(now.date(), publish_t, tz)
    if candidate < now + lead:
        candidate += timedelta(days=1)

    last_raw = db.last_publish_at()
    if last_raw:
        last = datetime.fromisoformat(last_raw)
        if candidate <= last:
            candidate = datetime.combine(
                (last + timedelta(days=1)).date(), publish_t, last.tzinfo
            )
    return candidate


def upload_clip(cfg: Config, db: State, clip) -> str | None:
    """Sube un clip como privado con publicación programada. Devuelve el video id."""
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = get_credentials(cfg, interactive=False)
    youtube = build("youtube", "v3", credentials=creds)

    publish_at = _next_publish_slot(cfg, db)
    tags = db.clip_tags(clip) + list(cfg.get("upload.extra_tags", []))
    title = clip["title"][:93] + " #Shorts"
    description = (clip["description"] or "") + "\n\n" + " ".join(
        f"#{t.lstrip('#')}" for t in tags[:10]
    )

    body = {
        "snippet": {
            "title": title,
            "description": description[:4900],
            "tags": tags[:30],
            "categoryId": str(cfg.get("upload.category_id", "24")),
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(clip["path"], chunksize=8 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    print(f"  [youtube] subiendo {Path(clip['path']).name} ...")
    response = None
    while response is None:
        status, response = request.next_chunk()
    video_id = response["id"]

    # persistir INMEDIATAMENTE: el Short ya existe en YouTube (paso no
    # reversible) y clip_uploaded escribe clip + hueco en una transacción.
    # Cualquier print/notify va después; morir aquí ya no duplica Shorts.
    stamp = publish_at.isoformat()
    db.clip_uploaded(clip["id"], video_id, stamp)
    print(f"  [youtube] listo: https://youtu.be/{video_id} "
          f"(se publica {publish_at.strftime('%Y-%m-%d %H:%M %Z')})")
    return video_id, stamp


def upload_pending(cfg: Config, db: State) -> int:
    """Sube todos los clips renderizados que aún no están en YouTube."""
    # opt-in explícito: sin upload.enabled: true en la config no se sube nada
    if not cfg.get("upload.enabled", False):
        print("[4/4] Subida desactivada en config.yaml, se omite")
        return 0
    print("[4/4] Subiendo clips a YouTube...")
    # con revisión activada nada sale sin tu visto bueno (aurclips review)
    require_review = cfg.get("review.enabled", True)
    clips = db.clips_to_upload(require_review=require_review)
    if require_review:
        pending = len(db.clips_to_review())
        if pending:
            print(f"  {pending} clip(s) esperan revisión; corre "
                  f"'python -m aurclips review' para aprobarlos")
    if not clips:
        print("  nada pendiente por subir")
        return 0
    try:
        get_credentials(cfg, interactive=False)
    except RuntimeError as e:
        print(f"  [aviso] {e}")
        print("  los clips quedan en cola; se subirán cuando haya sesión")
        return 0

    from .notify import notify
    from .stats import order_pending

    # prioriza los clips más prometedores (rendimiento histórico + puntuación)
    clips = order_pending(db, clips)
    cap = cfg.get("limits.max_uploads_per_run", 5)
    if len(clips) > cap:
        print(f"  {len(clips)} en cola; se suben {cap} (limits.max_uploads_per_run)")
        clips = clips[:cap]

    uploaded = 0
    for clip in clips:
        try:
            video_id, publish_at = upload_clip(cfg, db, clip)
            notify(cfg, "uploaded",
                   f"'{clip['title']}' programado para {publish_at} "
                   f"— https://youtu.be/{video_id}")
            uploaded += 1
        except Exception as e:  # noqa: BLE001 — seguir con los demás clips
            print(f"  [error] clip {clip['id']}: {e}")
            db.clip_failed(clip["id"], str(e))
            notify(cfg, "error", f"Falló la subida del clip '{clip['title']}': "
                                 f"{str(e)[:200]}")
    print(f"  {uploaded} clip(s) subidos y programados")
    return uploaded
