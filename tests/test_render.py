"""Tests de las rutas que produce el renderizador.

Seam bajo test: ``clip_paths`` (config + identidad del clip -> carpeta de
trabajo y ruta del mp4). Es lo que decide dónde cae cada archivo, y es lo que
permite que un recorte suelto no pise nunca un clip del pipeline. El quemado
con ffmpeg queda fuera: aquí no se renderiza nada.
"""

from pathlib import Path

import yaml

from aurclips.config import Config
from aurclips.render import clip_paths


def _cfg(tmp_path: Path) -> Config:
    """Config mínima con las carpetas de datos dentro de tmp."""
    doc = {"paths": {"work": str(tmp_path / "work"),
                     "output": str(tmp_path / "output")}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return Config(path)


def test_sin_destino_las_rutas_son_las_del_pipeline(tmp_path):
    """El pipeline no pasa destino: sigue escribiendo donde escribía."""
    cfg = _cfg(tmp_path)
    workdir, out_path = clip_paths(cfg, 7, "Un título cualquiera")
    assert workdir == cfg.work_dir / "clip_7"
    assert out_path.parent == cfg.output_dir
    assert out_path.name.startswith("0007_")
    assert out_path.suffix == ".mp4"


def test_con_destino_el_mp4_cae_donde_se_le_diga(tmp_path):
    cfg = _cfg(tmp_path)
    destino = tmp_path / "recortes" / "mi_grabacion"
    workdir, out_path = clip_paths(cfg, 1, "Un título", out_dir=destino,
                                   work_name="suelto_mi_grabacion_1")
    assert out_path.parent == destino
    assert workdir == cfg.work_dir / "suelto_mi_grabacion_1"


def test_el_destino_se_crea_si_no_existe(tmp_path):
    cfg = _cfg(tmp_path)
    destino = tmp_path / "recortes" / "todavia_no_existe"
    _, out_path = clip_paths(cfg, 1, "Un título", out_dir=destino)
    assert out_path.parent.is_dir()


def test_un_recorte_suelto_nunca_pisa_un_clip_del_pipeline(tmp_path):
    """Misma numeración, distinto destino: los archivos no se tocan."""
    cfg = _cfg(tmp_path)
    _, del_pipeline = clip_paths(cfg, 1, "El mismo título")
    _, suelto = clip_paths(cfg, 1, "El mismo título",
                           out_dir=cfg.output_dir / "mi_grabacion",
                           work_name="suelto_mi_grabacion_1")
    assert del_pipeline != suelto


def test_dos_grabaciones_distintas_no_comparten_carpeta(tmp_path):
    cfg = _cfg(tmp_path)
    _, una = clip_paths(cfg, 1, "Título", out_dir=cfg.output_dir / "partida_uno")
    _, otra = clip_paths(cfg, 1, "Título", out_dir=cfg.output_dir / "partida_dos")
    assert una.parent != otra.parent


def test_el_titulo_se_sanea_para_que_sea_un_nombre_de_archivo(tmp_path):
    cfg = _cfg(tmp_path)
    _, out_path = clip_paths(cfg, 3, 'Un título: con "cosas" raras / y barras')
    assert ":" not in out_path.name
    assert "/" not in out_path.name
    assert '"' not in out_path.name
