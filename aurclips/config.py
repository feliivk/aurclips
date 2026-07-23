"""Carga de configuración (config.yaml) y resolución de rutas.

El proyecto corre de dos formas y ambas tienen que funcionar:

- **Checkout**: clonado del repo, con config.yaml y data/ en la raíz. Es como
  siempre lo ha usado el creador y no debe cambiar en nada.
- **Instalado** (`pip install`/`pipx`): el paquete vive en site-packages, así
  que config y datos NO pueden colgar de él. Van a las carpetas de usuario del
  SO (platformdirs), y el config se siembra del default empaquetado.

La regla es una sola: la base contra la que se resuelven las rutas relativas es
la carpeta del config.yaml que se cargó. En checkout esa carpeta es la raíz del
repo (idéntico a antes); en instalado es la carpeta de datos de usuario.
"""

from __future__ import annotations

import os
import shutil
from importlib import resources
from pathlib import Path

import yaml

# Raíz del checkout (padre del paquete). Solo tiene sentido corriendo desde el
# repo; instalado apunta a site-packages y por eso no se usa como base de datos.
ROOT = Path(__file__).resolve().parent.parent

APP_NAME = "aurclips"


def _exe_suffix() -> str:
    """Sufijo de los ejecutables en este SO ('.exe' en Windows, nada en POSIX)."""
    return ".exe" if os.name == "nt" else ""


class Config:
    def __init__(self, path: Path | None = None):
        if path is not None:
            self.path = Path(path)
            self._base = self.path.parent
        else:
            self.path, self._base = self._discover()
        with open(self.path, "r", encoding="utf-8") as f:
            self.raw: dict = yaml.safe_load(f) or {}

    # --- descubrimiento del config y su base -----------------------------
    @staticmethod
    def _checkout_config() -> Path | None:
        """El config.yaml del repo, si estamos corriendo desde el checkout."""
        candidate = ROOT / "config.yaml"
        return candidate if candidate.is_file() else None

    @classmethod
    def _discover(cls) -> tuple[Path, Path]:
        """(ruta del config, carpeta base) según el modo de ejecución.

        Orden: AURCLIPS_HOME > config del checkout (comportamiento de siempre) >
        ./config.yaml del cwd > carpeta de usuario del SO (se siembra si falta).
        """
        home = os.environ.get("AURCLIPS_HOME")
        if home:
            base = Path(home)
            return base / "config.yaml", base

        checkout = cls._checkout_config()
        if checkout is not None:
            return checkout, checkout.parent

        cwd_config = Path.cwd() / "config.yaml"
        if cwd_config.is_file():
            return cwd_config, cwd_config.parent

        # modo instalado: config en la carpeta de usuario, datos en la suya
        import platformdirs
        config_path = Path(platformdirs.user_config_dir(APP_NAME)) / "config.yaml"
        if not config_path.exists():
            cls._seed_default_config(config_path)
        return config_path, Path(platformdirs.user_data_dir(APP_NAME))

    @staticmethod
    def _seed_default_config(dest: Path) -> None:
        """Copia el config.yaml de ejemplo empaquetado al dir de usuario."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        default = resources.files(APP_NAME) / "assets" / "config.default.yaml"
        dest.write_text(default.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[config] primer arranque: config creado en {dest}\n"
              f"         edítalo (channel.angle, selection.profile) y vuelve a correr.")

    # --- acceso genérico -------------------------------------------------
    def get(self, dotted: str, default=None):
        node = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def override(self, dotted: str, value) -> None:
        """Pisa una clave solo en esta corrida. No toca config.yaml.

        Para mandos de línea de comandos que valen para una ejecución y no
        deben quedarse escritos (el tope de recortes de `clip`, por ejemplo).
        """
        parts = dotted.split(".")
        node = self.raw
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value

    # --- rutas -----------------------------------------------------------
    def _dir(self, key: str, default: str) -> Path:
        # las rutas absolutas mandan; las relativas cuelgan de la base (la
        # carpeta del config: raíz del repo en checkout, dir de datos instalado)
        p = self._base / self.get(f"paths.{key}", default)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def data_dir(self) -> Path:
        return self._dir("data", "data")

    @property
    def inbox_dir(self) -> Path:
        return self._dir("inbox", "data/inbox")

    @property
    def downloads_dir(self) -> Path:
        return self._dir("downloads", "data/downloads")

    @property
    def work_dir(self) -> Path:
        return self._dir("work", "data/work")

    @property
    def output_dir(self) -> Path:
        return self._dir("output", "data/output")

    @property
    def credentials_dir(self) -> Path:
        return self._dir("credentials", "credentials")

    @property
    def logs_dir(self) -> Path:
        return self._dir("logs", "logs")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "state.db"

    # --- ffmpeg ----------------------------------------------------------
    def _tool(self, name: str) -> str:
        # binario empaquetado si lo hay (nombre con sufijo por SO), y si no, el
        # del sistema por PATH — que es la vía normal en Linux/macOS.
        # Se mira bajo la base y bajo la raíz del repo: así el ffmpeg
        # vendorizado en tools/ del checkout se encuentra aunque el config
        # venga de otra carpeta (p.ej. --config o un tmp de test).
        rel = self.get("paths.ffmpeg", "tools/ffmpeg/bin")
        filename = f"{name}{_exe_suffix()}"
        # base y ROOT, sin repetir cuando coinciden (checkout: _base == ROOT)
        for base in dict.fromkeys([self._base, ROOT]):
            bundled = base / rel / filename
            if bundled.exists():
                return str(bundled)
        on_path = shutil.which(name)
        if on_path:
            return on_path
        raise FileNotFoundError(
            f"No se encontró {name}. Instálalo y agrégalo al PATH "
            f"(macOS: brew install ffmpeg · Linux: apt install ffmpeg · "
            f"Windows: winget install ffmpeg)."
        )

    @property
    def ffmpeg(self) -> str:
        return self._tool("ffmpeg")

    @property
    def ffprobe(self) -> str:
        return self._tool("ffprobe")
