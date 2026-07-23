"""Registro de eventos y avisos opcionales por Discord.

Cada evento relevante del bot (subidas, errores, etc.) queda registrado en
``logs/events.log`` y, si hay un webhook de Discord configurado, se envía además
un aviso. Un webhook caído nunca debe interrumpir el pipeline: cualquier fallo
de red se ignora con un simple aviso por consola.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT


def _log_path(cfg) -> Path:
    """Ruta de ``events.log``, creando la carpeta si hace falta.

    Es ``cfg.logs_dir`` (``logs/`` bajo la base: raíz del repo en checkout, dir
    de datos de usuario instalado; redirigible con ``paths.logs``). Sin cfg cae
    a ``logs/`` bajo ROOT."""
    try:
        if cfg is not None:
            return cfg.logs_dir / "events.log"
    except Exception:
        pass
    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "events.log"


def notify(cfg, event: str, message: str) -> None:
    """Registra el evento y, si corresponde, avisa por Discord.

    - SIEMPRE: agrega línea ``[ISO-timestamp] [event] message`` a
      ``logs/events.log`` (crea la carpeta ``logs/`` bajo ROOT si no existe).
    - Si ``cfg.get("alerts.discord_webhook")`` tiene URL y ``event`` está en
      ``cfg.get("alerts.notify_on", ["error", "uploaded"])``: hace un POST con
      JSON ``{"content": f"**[{event}]** {message}"}`` usando urllib (timeout
      10s). Cualquier fallo del webhook se ignora con un print de aviso, nunca
      rompe el pipeline."""
    # --- 1) registro permanente en events.log ---------------------------
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] [{event}] {message}"
    try:
        with open(_log_path(cfg), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:  # noqa: BLE001 — no poder loguear no debe romper nada
        print(f"  [aviso] no se pudo escribir en events.log: {e}")

    # --- 2) aviso opcional por Discord ----------------------------------
    try:
        webhook = cfg.get("alerts.discord_webhook")
    except Exception:
        webhook = None
    if not webhook:
        return

    try:
        notify_on = cfg.get("alerts.notify_on", ["error", "uploaded"])
    except Exception:
        notify_on = ["error", "uploaded"]
    if not notify_on or event not in notify_on:
        return

    payload = json.dumps({"content": f"**[{event}]** {message}"}).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as e:  # noqa: BLE001 — un webhook caído nunca rompe el bot
        print(f"  [aviso] no se pudo enviar el aviso a Discord: {e}")
