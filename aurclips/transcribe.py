"""Transcripción local con faster-whisper (timestamps por palabra).

Usa la GPU NVIDIA si está disponible (las DLLs de CUDA vienen en las wheels
nvidia-cublas-cu12 / nvidia-cudnn-cu12); si la GPU falla, cae a CPU solo.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from .config import Config

_model_cache: dict[tuple, object] = {}
# Pistas de que un fallo al construir el modelo es por la GPU y conviene caer a
# CPU. Incluye tanto términos de Windows ('dll') como de Linux (cargar una .so
# de CUDA aflora como 'cannot open shared object file').
_GPU_ERROR_HINTS = ("cublas", "cudnn", "cuda", "dll", "device",
                    "shared object", "libcu", ".so")

# Muestra por extremo/centro con que se identifica una grabación. Hashear
# varios GB tardaría más que lo que la caché ahorra, así que se muestrea:
# tamaño + principio + centro + final. Dos archivos del mismo tamaño que solo
# difieran fuera de las muestras se considerarían el mismo — en la práctica eso
# es un recodificado, no una grabación distinta.
SAMPLE_BYTES = 1024 * 1024


def _register_cuda_dlls():
    """Registra las DLLs de CUDA instaladas vía pip. Solo aplica en Windows.

    En Linux/macOS no se hace nada: `os.add_dll_directory` no existe fuera de
    Windows, y las libs de CUDA (.so) las resuelve el linker del sistema o
    LD_LIBRARY_PATH — no un registro en caliente. Aislarlo tras el guard deja
    claro que es un shim de Windows, no código muerto por accidente.
    """
    if os.name != "nt":
        return
    import sysconfig
    base = Path(sysconfig.get_paths()["purelib"]) / "nvidia"
    for sub in ("cublas", "cudnn"):
        d = base / sub / "bin"
        if d.is_dir():
            os.add_dll_directory(str(d))
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


def _cuda_available() -> bool:
    """¿Hay una GPU CUDA utilizable? Sondeo explícito, sin olfatear excepciones."""
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001 — sin ctranslate2/CUDA: simplemente no hay
        return False


def _pick_device(cfg: Config, force_cpu: bool, cuda_available: bool) -> tuple[str, str]:
    """(device, compute_type) para WhisperModel. Puro: sin cargar nada.

    Si se pide 'auto'/'cuda' pero no hay GPU, se pide 'cpu' explícito en vez de
    dejar que la construcción del modelo intente CUDA y falle (frecuente en
    Linux/macOS). Con GPU presente, la elección del usuario se respeta intacta.
    """
    if force_cpu:
        return "cpu", "int8"
    device = cfg.get("whisper.device", "auto")
    compute = cfg.get("whisper.compute_type", "auto")
    if device in ("auto", "cuda") and not cuda_available:
        device = "cpu"
    return device, compute


def _looks_like_gpu_error(exc: Exception) -> bool:
    return any(hint in str(exc).lower() for hint in _GPU_ERROR_HINTS)


def _get_model(cfg: Config, force_cpu: bool = False):
    from faster_whisper import WhisperModel  # import perezoso: tarda en cargar

    _register_cuda_dlls()
    device, compute = _pick_device(cfg, force_cpu, _cuda_available())
    key = (cfg.get("whisper.model", "small"), device, compute)
    if key not in _model_cache:
        print(f"  [whisper] cargando modelo '{key[0]}' ({device}/{compute})...")
        _model_cache[key] = WhisperModel(key[0], device=device, compute_type=compute)
    return _model_cache[key]


def _run(model, cfg: Config, video_path: str) -> dict:
    language = cfg.get("whisper.language") or None
    segments_iter, info = model.transcribe(
        video_path,
        language=language,
        word_timestamps=True,
        vad_filter=True,
    )
    segments = []
    for seg in segments_iter:
        words = [
            {"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word.strip()}
            for w in (seg.words or [])
            if w.word.strip()
        ]
        segments.append({
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "words": words,
        })
        if len(segments) % 100 == 0:
            print(f"  [whisper] ... {seg.end/60:.1f} min transcritos")
    return {"language": info.language, "segments": segments}


# ---------------------------------------------------------------------------
# Caché: una grabación se transcribe una sola vez
# ---------------------------------------------------------------------------

def content_key(cfg: Config, video_path: str | Path) -> str:
    """Identidad de una grabación de cara a la caché.

    Es el contenido lo que identifica, no la ruta: renombrar o mover un archivo
    no obliga a transcribirlo otra vez. El modelo y el idioma forzado entran en
    la clave porque cambiarlos cambia el resultado — bajar de 'medium' a
    'small' no puede servir la transcripción vieja.
    """
    path = Path(video_path)
    size = path.stat().st_size
    digest = hashlib.sha256()
    digest.update(f"{size}|{cfg.get('whisper.model', 'small')}|"
                  f"{cfg.get('whisper.language') or 'auto'}".encode())
    offsets = (0, max(0, size // 2 - SAMPLE_BYTES // 2), max(0, size - SAMPLE_BYTES))
    with open(path, "rb") as f:
        for offset in offsets:
            f.seek(offset)
            digest.update(f.read(SAMPLE_BYTES))
    return digest.hexdigest()[:20]


def cache_path(cfg: Config, key: str) -> Path:
    return cfg.work_dir / "transcripts" / f"{key}.json"


def cached_transcript(cfg: Config, video_path: str | Path) -> dict | None:
    """La transcripción ya hecha de esta grabación, o None si no hay."""
    path = cache_path(cfg, content_key(cfg, video_path))
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # una caché a medias (corrida interrumpida) no vale, pero tampoco
        # justifica romper la corrida: se transcribe de nuevo
        return None


def store_transcript(cfg: Config, video_path: str | Path, result: dict) -> Path:
    path = cache_path(cfg, content_key(cfg, video_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    return path


def transcribe(cfg: Config, video_path: str, out_json: Path | None = None) -> dict:
    """Transcribe una grabación (o reutiliza lo ya transcrito) y la devuelve.

    Estructura: {"language": str, "segments": [{"start","end","text",
    "words": [{"start","end","word"}]}]}

    Con ``out_json`` deja además una copia ahí, que es como el pipeline espera
    encontrar su transcript.json.
    """
    result = cached_transcript(cfg, video_path)
    if result is not None:
        print(f"  [whisper] ya transcrito ({len(result['segments'])} segmentos); "
              f"se reutiliza")
    else:
        try:
            result = _run(_get_model(cfg), cfg, video_path)
        except (RuntimeError, OSError) as e:
            # en Linux un fallo de CUDA aflora como OSError al cargar la .so, no
            # solo como RuntimeError; si no huele a GPU, no lo tapamos
            if not _looks_like_gpu_error(e):
                raise
            print(f"  [whisper] GPU no disponible ({e}); reintentando en CPU...")
            result = _run(_get_model(cfg, force_cpu=True), cfg, video_path)
        store_transcript(cfg, video_path, result)
        print(f"  [whisper] {len(result['segments'])} segmentos, "
              f"idioma detectado: {result['language']}")

    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
    return result
