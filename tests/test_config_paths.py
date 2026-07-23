"""Tests de la resolución de rutas de Config.

Seam bajo test: ``Config`` (de dónde sale config.yaml y contra qué base se
resuelven las rutas relativas). Lo que se afirma es el contrato del port:

- Un config explícito manda, y sus rutas relativas cuelgan de la carpeta del
  propio config — así el modo checkout (config.yaml en la raíz del repo) resuelve
  data/ como hermano, idéntico a como venía siendo.
- Las rutas absolutas en paths.* se respetan tal cual (de esto dependen el resto
  de los tests del repo).
- En modo instalado (sin config a mano) la base son las carpetas de usuario del
  SO, no site-packages.

Sin tocar el disco de config real del usuario: todo va a tmp_path o a un
AURCLIPS_HOME apuntado a tmp.
"""

from pathlib import Path

import yaml

from aurclips.config import Config


def _write(path: Path, doc: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def test_un_config_explicito_resuelve_relativos_contra_su_carpeta(tmp_path):
    """El caso del checkout: config.yaml en una carpeta, data/ colgando de ahí."""
    cfg_path = _write(tmp_path / "config.yaml", {})
    cfg = Config(cfg_path)
    assert cfg.data_dir == tmp_path / "data"
    assert cfg.inbox_dir == tmp_path / "data" / "inbox"
    assert cfg.db_path == tmp_path / "data" / "state.db"


def test_las_rutas_absolutas_se_respetan(tmp_path):
    """Los tests del repo pasan paths absolutos; no deben reanclarse a la base."""
    work = tmp_path / "otro_sitio" / "work"
    cfg_path = _write(tmp_path / "config.yaml", {"paths": {"work": str(work)}})
    cfg = Config(cfg_path)
    assert cfg.work_dir == work


def test_paths_relativos_personalizados_cuelgan_de_la_base(tmp_path):
    cfg_path = _write(tmp_path / "config.yaml",
                      {"paths": {"output": "salidas/shorts"}})
    cfg = Config(cfg_path)
    assert cfg.output_dir == tmp_path / "salidas" / "shorts"


def test_pedir_un_dir_lo_crea(tmp_path):
    cfg = Config(_write(tmp_path / "config.yaml", {}))
    assert cfg.output_dir.is_dir()


def test_aurclips_home_apunta_la_base(tmp_path, monkeypatch):
    """AURCLIPS_HOME manda sobre el descubrimiento automático."""
    home = tmp_path / "mi_home"
    _write(home / "config.yaml", {"channel": {"angle": "prueba"}})
    monkeypatch.setenv("AURCLIPS_HOME", str(home))
    cfg = Config()
    assert cfg.get("channel.angle") == "prueba"
    assert cfg.data_dir == home / "data"


def test_sin_config_a_mano_se_siembra_en_carpeta_de_usuario(tmp_path, monkeypatch):
    """Modo instalado: sin config, se crea uno en el dir de usuario (no en site-packages)."""
    import platformdirs
    fake_user = tmp_path / "user_config"
    monkeypatch.delenv("AURCLIPS_HOME", raising=False)
    monkeypatch.setattr(platformdirs, "user_config_dir",
                        lambda *a, **k: str(fake_user))
    # y que no encuentre un config de checkout: cwd a un sitio limpio
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Config, "_checkout_config", staticmethod(lambda: None))
    cfg = Config()
    assert (fake_user / "config.yaml").exists()
    assert Path(cfg.path) == fake_user / "config.yaml"


def test_la_base_de_datos_en_modo_usuario_no_es_site_packages(tmp_path, monkeypatch):
    import platformdirs
    monkeypatch.delenv("AURCLIPS_HOME", raising=False)
    monkeypatch.setattr(platformdirs, "user_config_dir",
                        lambda *a, **k: str(tmp_path / "cfg"))
    monkeypatch.setattr(platformdirs, "user_data_dir",
                        lambda *a, **k: str(tmp_path / "dat"))
    monkeypatch.setattr(Config, "_checkout_config", staticmethod(lambda: None))
    monkeypatch.chdir(tmp_path)
    cfg = Config()
    assert str(tmp_path / "dat") in str(cfg.data_dir)
    assert "site-packages" not in str(cfg.data_dir)


def test_los_assets_empaquetados_son_alcanzables():
    """La siembra del config y la fuente se resuelven por importlib.resources.
    Si el empaquetado deja de incluir aurclips/assets, el modo instalado
    revienta al primer arranque — este test lo caza sin construir un wheel."""
    from importlib import resources
    assets = resources.files("aurclips") / "assets"
    assert (assets / "config.default.yaml").is_file()
    assert (assets / "fonts" / "Anton-Regular.ttf").is_file()
