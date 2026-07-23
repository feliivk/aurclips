"""Títulos y descripción escritos por un LLM local (Ollama).

Aquí el LLM **no elige clips** — esa decisión se queda en el selector, simple
y auditable. Lo que hace bien un modelo pequeño es redactar: recibe la
transcripción *completa* del clip (no la primera frase), el ángulo de tu canal
y unos títulos tuyos de ejemplo, y propone. Tú apruebas con ``aurclips review``.

Todo local y gratis: si Ollama no está corriendo o falla, se conserva la
metadata heurística sin romper la corrida.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import List

from pydantic import BaseModel

from .config import Config

DEFAULT_MODEL = "qwen2.5:7b"
MAX_TITLE_CHARS = 80


class Proposal(BaseModel):
    titulo: str
    descripcion: str
    hashtags: List[str]


SYSTEM = """\
Escribes títulos para YouTube Shorts de UN canal concreto. Recibes la
transcripción completa de un clip ya recortado y devuelves su metadata.

Reglas:
- El título nombra la idea concreta del clip, no el tema general.
- Máximo 80 caracteres, sin comillas, sin emojis, sin '#', sin 'Shorts'.
- Nada de clickbait que el clip no cumpla: si el clip no lo dice, no lo prometas.
- No empieces con muletillas ("y", "pero", "bueno", "entonces").
- Escribe en el MISMO idioma de la transcripción.
- La descripción: 1-2 frases completas que den contexto.
- 3-5 hashtags específicos del contenido, sin '#' y sin genéricos ("video",
  "clip", "shorts").
Responde solo con el JSON pedido.
"""


def _endpoint(cfg: Config) -> str:
    return cfg.get("titles.url", "http://localhost:11434").rstrip("/")


def available(cfg: Config) -> bool:
    """¿Hay un Ollama respondiendo? (3 s de paciencia, no más)."""
    try:
        with urllib.request.urlopen(f"{_endpoint(cfg)}/api/tags", timeout=3):
            return True
    except (urllib.error.URLError, OSError):
        return False


def enabled(cfg: Config) -> bool:
    """¿Toca pedirle los títulos al LLM en esta corrida?"""
    engine = cfg.get("titles.engine", "auto")
    if engine == "heuristic":
        return False
    if not available(cfg):
        if engine == "ollama":
            print("  [títulos] Ollama no responde; se usan títulos heurísticos")
        return False
    return True


def _context(cfg: Config) -> list[str]:
    """Ángulo del canal y ejemplos: lo que convierte 'un título' en el tuyo."""
    lines: list[str] = []
    angle = (cfg.get("channel.angle") or "").strip()
    if angle:
        lines += [f"Canal: {angle}", ""]
    examples = [str(e).strip() for e in (cfg.get("channel.title_examples") or [])
                if str(e).strip()]
    if examples:
        lines.append("Títulos de este canal (imita el tono, no el tema):")
        lines += [f"- {e}" for e in examples[:5]]
        lines.append("")
    return lines


def propose(cfg: Config, clip_text: str,
            video_title: str = "") -> tuple[str, str, list[str]] | None:
    """(título, descripción, hashtags) del LLM local; None si no se pudo."""
    model = cfg.get("titles.model", DEFAULT_MODEL)
    parts = _context(cfg)
    if video_title:
        parts += [f"Video de origen: {video_title}", ""]
    parts += ["Transcripción completa del clip:", clip_text.strip()]

    # temperatura y seed configurables: con titles.seed fijo (y la temperatura
    # que sea), Ollama repite la misma propuesta para el mismo clip — es lo
    # que hace reproducible la única pieza del pipeline con LLM. El default
    # sigue siendo creativo (sin seed).
    options = {"num_ctx": 8192,
               "temperature": cfg.get("titles.temperature", 0.7)}
    seed = cfg.get("titles.seed")
    if seed is not None:
        options["seed"] = int(seed)

    payload = json.dumps({
        "model": model,
        "stream": False,
        "format": Proposal.model_json_schema(),
        "options": options,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "\n".join(parts)},
        ],
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{_endpoint(cfg)}/api/chat", data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
        out = Proposal.model_validate_json(data["message"]["content"])
    except Exception as e:  # noqa: BLE001 — cualquier fallo => metadata heurística
        print(f"  [títulos] {model} falló ({e}); se conserva el título heurístico")
        return None

    from .safety import strip_mild

    title = strip_mild(out.titulo).strip().strip('"').strip()
    if not title:
        return None
    if len(title) > MAX_TITLE_CHARS:
        title = title[:MAX_TITLE_CHARS].rsplit(" ", 1)[0].rstrip(".,;: ")
    tags = [h.lstrip("#").strip() for h in out.hashtags if h.strip()][:5]
    return title, out.descripcion.strip(), tags
