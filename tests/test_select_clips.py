"""Tests del seleccionador de clips.

Seam bajo test: select_clips (config + transcripción + ruta de video ->
lista de Clips). Sin video real: ffprobe degrada a la duración de la
transcripción y la energía de audio a neutra (contrato de test). Sin
Ollama: titles.engine "heuristic" y URL en puerto muerto por si acaso.
"""

from pathlib import Path

import yaml

from aurclips.config import Config
from aurclips.heuristics import MIN_GAP_S
from aurclips.select_clips import select_clips


def _cfg(tmp_path: Path, paths: dict | None = None, **selection) -> Config:
    """Config mínima en tmp con la selección forzada a heurística pura."""
    sel = {
        "min_clip_seconds": 15,
        "max_clip_seconds": 59,
    }
    sel.update(selection)
    doc = {"selection": sel,
           "titles": {"engine": "heuristic", "url": "http://127.0.0.1:9"}}
    if paths:
        doc["paths"] = paths
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return Config(path)


def _seg(start: float, end: float, text: str) -> dict:
    """Segmento sintético con palabras repartidas uniformemente."""
    tokens = text.split()
    dur = (end - start) / len(tokens)
    return {
        "start": start, "end": end, "text": text,
        "words": [{"word": w, "start": start + i * dur,
                   "end": start + (i + 1) * dur}
                  for i, w in enumerate(tokens)],
    }

FLAT = "palabras normales que rellenan la charla continua sin nada especial"


def _transcript(total_s: float, seg_s: float = 10.0) -> dict:
    """Charla continua y pareja: mismo texto y ritmo en todo el video."""
    segs = [_seg(t, min(t + seg_s, total_s), FLAT)
            for t in range(0, int(total_s), int(seg_s))]
    return {"segments": segs}


NO_VIDEO = "no_existe.mp4"


def test_la_densidad_manda_cuando_el_tope_es_mas_alto(tmp_path):
    # 16 min de charla pareja con tope de 5: la densidad por defecto
    # (1 Short por cada 4 min) fija el objetivo en 4, no en el tope;
    # y los elegidos no se traslapan y respetan la separación mínima
    cfg = _cfg(tmp_path, clips_per_video=5)
    clips = select_clips(cfg, _transcript(960), "video largo", NO_VIDEO)
    assert len(clips) == 4
    for a, b in zip(clips, clips[1:]):
        assert b.start_s >= a.end_s + MIN_GAP_S


HOT = "cuidado este secreto es increible ¿nadie lo sabia? ¡brutal de verdad!"


def test_umbral_de_calidad_descarta_candidatos_flojos(tmp_path):
    # 10 min de charla plana con UNA sola zona con gancho real: con el
    # umbral por defecto sale solo esa, aunque el objetivo de densidad sea 2
    cfg = _cfg(tmp_path, clips_per_video=3)
    tr = _transcript(600)
    tr["segments"][30] = _seg(300.0, 310.0, HOT)  # minuto 5
    clips = select_clips(cfg, tr, "video con un solo momento bueno", NO_VIDEO)
    assert len(clips) == 1
    assert clips[0].start_s <= 300 < clips[0].end_s


def test_sin_umbral_se_alcanza_el_objetivo_de_densidad(tmp_path):
    # el mismo video con quality_floor desactivado rellena el objetivo (2):
    # prueba de que el umbral es lo que descarta, no otra cosa
    cfg = _cfg(tmp_path, clips_per_video=3, quality_floor=0.0)
    tr = _transcript(600)
    tr["segments"][30] = _seg(300.0, 310.0, HOT)
    clips = select_clips(cfg, tr, "mismo video sin filtro", NO_VIDEO)
    assert len(clips) == 2


def test_video_corto_se_usa_completo(tmp_path):
    # el atajo existente no cambia: lo que ya cabe como Short va entero
    cfg = _cfg(tmp_path)
    clips = select_clips(cfg, _transcript(30), "clip corto", NO_VIDEO)
    assert len(clips) == 1
    assert clips[0].start_s == 0.0
    assert abs(clips[0].end_s - 30) < 1e-6


def test_el_tope_recorta_la_densidad(tmp_path):
    # 16 min pedirían 4 por densidad, pero el tope clips_per_video es 2
    cfg = _cfg(tmp_path, clips_per_video=2)
    clips = select_clips(cfg, _transcript(960), "tope bajo", NO_VIDEO)
    assert len(clips) == 2


def test_densidad_configurable(tmp_path):
    # con 1 Short por cada 8 min, 16 min dan 2 aunque el tope permita 5
    cfg = _cfg(tmp_path, clips_per_video=5, minutes_per_short=8)
    clips = select_clips(cfg, _transcript(960), "densidad baja", NO_VIDEO)
    assert len(clips) == 2


def test_sin_binarios_de_ffmpeg_tambien_degrada(tmp_path):
    # máquina sin ffmpeg (ruta inexistente; tampoco en PATH): el selector
    # degrada igual que sin video — duración de la transcripción, energía neutra
    cfg = _cfg(tmp_path, paths={"ffmpeg": "no/existe"}, clips_per_video=5)
    clips = select_clips(cfg, _transcript(960), "sin ffmpeg", NO_VIDEO)
    assert len(clips) == 4


# --- ticket 02: estructura narrativa -----------------------------------

def _con_zonas(zona_a: tuple[str, str], zona_b: tuple[str, str]) -> dict:
    """240 s de charla plana con dos zonas de dos segmentos: A en el minuto
    1 (60-80 s) y B en el minuto 3 (180-200 s). Objetivo de densidad: 1 clip,
    así que el elegido revela cuál puntúa mejor."""
    tr = _transcript(240)
    tr["segments"][6] = _seg(60.0, 70.0, zona_a[0])
    tr["segments"][7] = _seg(70.0, 80.0, zona_a[1])
    tr["segments"][18] = _seg(180.0, 190.0, zona_b[0])
    tr["segments"][19] = _seg(190.0, 200.0, zona_b[1])
    return tr


def test_arrancar_con_muletilla_pierde(tmp_path):
    # mismas frases con y sin muletilla de arranque: gana la que engancha
    # desde la primera palabra (la zona con muletilla va ANTES, así que sin
    # penalización ganaría por orden)
    cfg = _cfg(tmp_path)
    # misma densidad en ambas zonas (los arranques son stopwords en las dos);
    # solo difiere que "bueno pues..." es muletilla y "ahora ya..." no
    tr = _con_zonas(
        ("bueno pues entonces mira este secreto importante hoy",
         "esa tecnica cambia todos los resultados del canal."),
        ("ahora ya esto mira este secreto importante hoy",
         "esa tecnica cambia todos los resultados del canal."),
    )
    clips = select_clips(cfg, tr, "muletilla vs directo", NO_VIDEO)
    assert len(clips) == 1
    assert clips[0].start_s >= 170


def test_relleno_de_stopwords_pierde_contra_contenido(tmp_path):
    # mismo ritmo y cierre, pero una zona es puro relleno y la otra dice
    # cosas: gana el contenido (el relleno va ANTES; sin bonus de densidad
    # ganaría por orden)
    cfg = _cfg(tmp_path)
    tr = _con_zonas(
        ("si lo que pasa es que ya se lo que te digo",
         "y eso es lo que hay al final de todo esto."),
        ("descubrimos estrategias concretas resultados sorprendentes datos"
         " reales metodos probados casos practicos utiles",
         "estas tecnicas funcionan siempre en cualquier canal grande cada"
         " semana."),
    )
    clips = select_clips(cfg, tr, "relleno vs contenido", NO_VIDEO)
    assert len(clips) == 1
    assert clips[0].start_s >= 170


def test_cerrar_la_idea_gana(tmp_path):
    # mismas palabras, pero una zona muere a mitad de frase y la otra
    # cierra: gana la que cierra (la abierta va ANTES)
    cfg = _cfg(tmp_path)
    tr = _con_zonas(
        ("descubrimos estrategias concretas resultados sorprendentes hoy",
         "estas tecnicas funcionan siempre en cualquier canal grande pero"),
        ("descubrimos estrategias concretas resultados sorprendentes hoy",
         "estas tecnicas funcionan siempre en cualquier canal grande."),
    )
    clips = select_clips(cfg, tr, "abierta vs cerrada", NO_VIDEO)
    assert len(clips) == 1
    assert clips[0].start_s >= 170


def test_el_final_cae_en_cierre_de_frase(tmp_path):
    # zona de 3 segmentos: gancho -> cierre "." -> cola densa sin cerrar.
    # Gane la ventana que gane, el clip debe terminar en el cierre (80 s):
    # o la puntuación prefirió la cerrada, o el recorte devolvió el final ahí
    cfg = _cfg(tmp_path)
    tr = _transcript(240)
    tr["segments"][6] = _seg(60.0, 70.0,
                             "cuidado con este secreto increible sobre el metodo nuevo")
    tr["segments"][7] = _seg(70.0, 80.0,
                             "la tecnica completa funciona sola y produce resultados grandes.")
    tr["segments"][8] = _seg(80.0, 90.0,
                             "¿cierto? ¿seguro? ¿facil? metodos estrategias resultados"
                             " concretos datos casos practicos reales grandes utiles")
    clips = select_clips(cfg, tr, "cola sin cerrar", NO_VIDEO)
    assert len(clips) == 1
    assert clips[0].start_s == 60.0
    assert clips[0].end_s == 80.0


def test_sin_corte_valido_se_conserva_el_final(tmp_path):
    # la única frase cerrada de la zona queda por debajo de la duración
    # mínima: el recorte no puede actuar y el final original se conserva
    cfg = _cfg(tmp_path)
    tr = _transcript(240)
    tr["segments"][6] = _seg(60.0, 70.0,
                             "¿cuidado con este secreto tan increible del metodo nuevo hoy?")
    tr["segments"][7] = _seg(70.0, 80.0,
                             "estrategias concretas resultados sorprendentes datos reales"
                             " metodos probados casos utiles")
    clips = select_clips(cfg, tr, "sin corte valido", NO_VIDEO)
    assert len(clips) == 1
    assert clips[0].start_s == 60.0
    assert clips[0].end_s == 80.0


# --- calibración por perfil / pesos -------------------------------------

# dos zonas equivalentes en ritmo y densidad: A engancha pero no cierra la
# idea; B cierra sin gancho. Cuál gana depende solo de los pesos.
ENGANCHA = ("cuidado con este secreto increible del metodo nuevo",
            "estrategias concretas resultados sorprendentes datos reales metodos probados")
CIERRA = ("estrategias concretas resultados sorprendentes datos reales metodos probados",
          "casos practicos utiles para cualquier canal grande enorme.")


def test_sin_peso_al_cierre_gana_el_gancho(tmp_path):
    cfg = _cfg(tmp_path, weights={"closes": 0.0})
    clips = select_clips(cfg, _con_zonas(ENGANCHA, CIERRA), "gancho", NO_VIDEO)
    assert len(clips) == 1
    assert clips[0].start_s == 60.0


def test_subir_el_peso_del_cierre_cambia_al_ganador(tmp_path):
    # mismo video, mismo motor: solo se recalibró una señal
    cfg = _cfg(tmp_path, weights={"closes": 0.9})
    clips = select_clips(cfg, _con_zonas(ENGANCHA, CIERRA), "cierre", NO_VIDEO)
    assert len(clips) == 1
    assert clips[0].end_s == 200.0


def test_perfil_desconocido_no_rompe(tmp_path):
    # un perfil mal escrito degrada al default en vez de tumbar la corrida
    cfg = _cfg(tmp_path, profile="no-existe", clips_per_video=2)
    assert select_clips(cfg, _transcript(960), "perfil raro", NO_VIDEO)


def test_sin_ventanas_utiles_no_salen_clips(tmp_path):
    # dos ráfagas de voz de 5 s muy separadas: ninguna ventana alcanza la
    # duración mínima -> 0 clips sin romper el pipeline
    cfg = _cfg(tmp_path)
    tr = {"segments": [_seg(0.0, 5.0, FLAT), _seg(100.0, 105.0, FLAT)]}
    assert select_clips(cfg, tr, "solo dos ráfagas", NO_VIDEO) == []
