"""Andamiaje de la corrida diaria: log por corrida, rotación y lock.

Esto vivía en `run.ps1` (PowerShell) y por eso la automatización era solo de
Windows. Al moverlo al CLI, la corrida programada es la misma en los tres SO y
el scheduler (Task Scheduler, cron, systemd, launchd) solo tiene que invocar
`aurclips run` — sin wrapper.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


def cadence_due(last_iso: str | None, every_hours: float,
                now: datetime) -> bool:
    """¿Ya toca? True si nunca corrió, o si pasaron ``every_hours`` desde
    ``last_iso``. Un timestamp ilegible cuenta como "nunca": mejor un trabajo
    de más que una cadencia muerta para siempre."""
    if not last_iso:
        return True
    try:
        last = datetime.fromisoformat(last_iso)
    except ValueError:
        return True
    return (now - last).total_seconds() >= every_hours * 3600


def last_run_info(log_dir: Path) -> tuple[str, bool] | None:
    """(log más reciente, ¿terminó bien?) de la última corrida o sesión watch.

    Es la primera pregunta de un pipeline desatendido: ¿anoche corrió y cómo
    terminó? "Bien" = el log cierra con la línea de despedida; una corrida
    muerta la delata la traza que cmd_run deja dentro del log. None si nunca
    ha corrido nada.
    """
    logs = list(log_dir.glob("run_*.log")) + list(log_dir.glob("watch_*.log"))
    if not logs:
        return None
    newest = max(logs, key=lambda p: p.stat().st_mtime)
    try:
        text = newest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return newest.name, False
    ok = ("Corrida completa." in text) or ("[watch] detenido" in text)
    return newest.name, ok


def dir_size(path: Path) -> int:
    """Bytes totales bajo una carpeta; 0 si no existe."""
    if not path.is_dir():
        return 0
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            continue
    return total


def prune_run_logs(log_dir: Path, keep: int = 30,
                   pattern: str = "run_*.log") -> int:
    """Conserva los `keep` logs más nuevos del patrón; borra el resto.

    Los nombres llevan fecha ordenable (YYYY-MM-DD_HHMM...), así que ordenar
    por nombre ordena por antigüedad. Devuelve cuántos borró."""
    logs = sorted(log_dir.glob(pattern))
    doomed = logs[:-keep] if keep else logs
    removed = 0
    for old in doomed:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed


class _Tee:
    """Escribe en varios streams a la vez (consola + archivo de log).

    Cualquier otro atributo (isatty, fileno, encoding, buffer...) se delega al
    primero, la consola, para que siga pareciendo un stream real: hay código
    en el pipeline (barras de progreso, libs que consultan isatty) que rompería
    la corrida si estos faltaran mientras stdout está intervenido."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)
            s.flush()
        return len(data)

    def flush(self):
        for s in self._streams:
            s.flush()

    def __getattr__(self, name):
        # solo se llama para atributos no definidos aquí: delega a la consola
        return getattr(self._streams[0], name)


@contextmanager
def tee_output(log_path: Path):
    """Duplica stdout/stderr a un archivo mientras siguen yendo a la consola."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(log_path, "w", encoding="utf-8")
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = _Tee(old_out, f)
    sys.stderr = _Tee(old_err, f)
    try:
        yield
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        f.close()


@contextmanager
def single_instance(lock_path: Path):
    """Exclusión entre corridas de `run`. Cede ``True`` si se adquirió el lock,
    ``False`` si ya hay otra corrida en marcha.

    Reemplaza el ``-MultipleInstances IgnoreNew`` del Task Scheduler: cron y
    launchd no evitan solapes, y dos `run` a la vez se pisan la misma state.db.
    El lock lo suelta el SO al cerrar el proceso, así que una corrida que muera
    no deja el candado echado."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a+")
    acquired = _try_lock(f)
    try:
        yield acquired
    finally:
        if acquired:
            _unlock(f)
        f.close()


def _try_lock(f) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(f) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
