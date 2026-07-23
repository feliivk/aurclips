"""Tests de la política de duplicados.

Seam bajo test: ``find_duplicate`` (texto + textos ya aceptados -> veredicto).
Es la política que comparten el pipeline y el modo recortador: uno le pasa los
clips de la base, el otro los recortes sueltos de la misma corrida. Aquí se
afirma la regla, no de dónde salen los textos.

``is_duplicate`` se prueba aparte, y solo para lo suyo: que sigue leyendo la
base y delegando en la misma política.
"""

from pathlib import Path
from types import SimpleNamespace

import yaml

from aurclips.config import Config
from aurclips.safety import find_duplicate, is_duplicate, screen_clip
from aurclips.state import State

# Jaccard sobre tokens de 3+ caracteres. Siete palabras compartidas sobre ocho
# distintas dan 0.875: por encima del umbral normal (0.8) y por debajo del
# exigente (0.9) que se aplica a los textos cortos. Es el par que separa las
# dos reglas.
SIETE = "alfa bravo charlie delta echo foxtrot golf"
OCHO = "alfa bravo charlie delta echo foxtrot golf hotel"
NUEVE = "alfa bravo charlie delta echo foxtrot golf hotel india"


def test_sin_textos_conocidos_nada_es_duplicado():
    assert find_duplicate("cualquier cosa que se diga aquí", [], 0.8) == (False, None)


def test_un_texto_distinto_no_es_duplicado():
    known = [(1, "hablando de cocinar pasta con salsa de tomate y albahaca")]
    assert find_duplicate(
        "una partida de ajedrez que termina en tablas por repetición",
        known, 0.8) == (False, None)


def test_un_texto_casi_identico_es_duplicado_y_dice_contra_cual():
    known = [(7, "el jefe final aparece justo cuando se acaban las pociones"),
             (9, "el jefe final aparece justo cuando se acaban las pociones y curas")]
    duplicate, clip_id = find_duplicate(
        "el jefe final aparece justo cuando se acaban las pociones", known, 0.8)
    assert duplicate
    assert clip_id == 7


def test_los_textos_cortos_exigen_mas_parecido_para_contar_como_duplicados():
    """Con pocas palabras el parecido engaña, así que el umbral sube 0.1."""
    # el texto nuevo tiene 7 tokens (corto) y se parece 0.875 al conocido
    assert find_duplicate(SIETE, [(1, OCHO)], 0.8) == (False, None)
    # el mismo parecido, con un texto lo bastante largo, sí cuenta
    duplicate, _ = find_duplicate(OCHO, [(1, NUEVE)], 0.8)
    assert duplicate


def test_un_texto_sin_palabras_utiles_no_es_duplicado_de_nada():
    """Solo tokens de 3+ caracteres cuentan; sin ninguno no hay qué comparar."""
    assert find_duplicate("y a mí no", [(1, "y a mí no")], 0.8) == (False, None)


def test_el_texto_conocido_vacio_no_dispara_falsos_positivos():
    assert find_duplicate("una frase con suficientes palabras para comparar",
                          [(1, "")], 0.8) == (False, None)


# --- las dos verificaciones juntas -----------------------------------------
# screen_clip es la política que comparten el pipeline y el modo recortador.
# Aquí se afirma qué pasa con un clip; que los dos modos la usen se ve en que
# ninguno tiene reglas propias.

LIMPIO = "una charla tranquila sobre estrategias y decisiones del juego"
NO_APTO = "el vecino guardaba cocaina debajo del sofá y nadie lo sabía"


def _cfg(tmp_path: Path, **doc) -> Config:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return Config(path)


def test_un_clip_limpio_y_nuevo_se_queda(tmp_path):
    verdict = screen_clip(_cfg(tmp_path), LIMPIO, [])
    assert verdict.keep
    assert not verdict.flagged
    assert verdict.unsafe_terms == []
    assert verdict.duplicate_of is None


def test_con_skip_el_clip_no_apto_se_va(tmp_path):
    cfg = _cfg(tmp_path, safety={"enabled": True, "action": "skip"})
    verdict = screen_clip(cfg, NO_APTO, [])
    assert not verdict.keep
    assert not verdict.flagged
    assert verdict.unsafe_terms  # el llamador puede decir por qué


def test_con_flag_el_clip_no_apto_se_queda_senalado(tmp_path):
    """Señalado no es descartado: lo aparta para que decidas tú."""
    cfg = _cfg(tmp_path, safety={"enabled": True, "action": "flag"})
    verdict = screen_clip(cfg, NO_APTO, [])
    assert verdict.keep
    assert verdict.flagged
    assert verdict.unsafe_terms


def test_un_clip_senalado_sigue_pasando_por_el_dedup(tmp_path):
    """Señalar no exime de duplicados: un repetido se va aunque esté marcado."""
    cfg = _cfg(tmp_path, safety={"enabled": True, "action": "flag"},
               dedup={"enabled": True, "similarity": 0.8})
    verdict = screen_clip(cfg, NO_APTO, [(3, NO_APTO)])
    assert not verdict.keep
    assert verdict.flagged
    assert verdict.duplicate_of == 3


def test_un_clip_repetido_se_va_y_dice_contra_cual(tmp_path):
    cfg = _cfg(tmp_path, dedup={"enabled": True, "similarity": 0.8})
    verdict = screen_clip(cfg, LIMPIO, [(42, LIMPIO)])
    assert not verdict.keep
    assert verdict.duplicate_of == 42


def test_sin_filtro_el_texto_no_se_mira(tmp_path):
    cfg = _cfg(tmp_path, safety={"enabled": False})
    verdict = screen_clip(cfg, NO_APTO, [])
    assert verdict.keep
    assert verdict.unsafe_terms == []


def test_sin_dedup_un_repetido_pasa(tmp_path):
    cfg = _cfg(tmp_path, dedup={"enabled": False})
    assert screen_clip(cfg, LIMPIO, [(1, LIMPIO)]).keep


# --- el envoltorio con base ------------------------------------------------

def test_is_duplicate_compara_contra_los_clips_de_la_base():
    """El envoltorio no tiene política propia: saca las filas y delega."""
    db = State(":memory:")
    video_id = db.add_video("local", "grabacion.mp4", "grabación",
                            "grabacion.mp4", 600.0)
    texto = "el jefe final aparece justo cuando se acaban las pociones"
    clip = SimpleNamespace(start=0.0, end=30.0, title="Un título",
                           description="Una descripción", tags=["gaming"],
                           score=1.0, marked=False)
    clip_id = db.add_clip(video_id, 0, clip, texto)

    assert is_duplicate(db, texto, 0.8) == (True, clip_id)
    assert is_duplicate(db, "algo completamente distinto sobre recetas de cocina",
                        0.8) == (False, None)
