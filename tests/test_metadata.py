"""Tests de la metadata heurística: título, descripción y hashtags.

Es el respaldo que se usa cuando no hay LLM local corriendo, así que tiene
que valerse sola. Seam bajo test: ``make_metadata`` (candidata -> título,
descripción, hashtags), sin video, sin red y sin Ollama.
"""

from aurclips.heuristics import Candidate, make_metadata


def _cand(*frases: str) -> Candidate:
    """Candidata sintética: una frase por segmento, 10 s cada uno."""
    segs = []
    for i, texto in enumerate(frases):
        tokens = texto.split()
        dur = 10 / len(tokens)
        segs.append({
            "start": i * 10.0, "end": (i + 1) * 10.0, "text": texto,
            "words": [{"word": w, "start": i * 10 + j * dur,
                       "end": i * 10 + (j + 1) * dur}
                      for j, w in enumerate(tokens)],
        })
    return Candidate(0.0, len(frases) * 10.0, 1.0, segs)


SOSA = "Hoy repasamos la configuracion basica del programa desde cero."
CON_GANCHO = "El error que casi todos cometen es dejar el valor por defecto."
CIERRE = "Cambiarlo toma diez segundos y arregla el problema entero."


def test_el_titulo_es_la_frase_con_gancho_no_la_primera():
    # la primera frase suele ser el arranque de la idea, no su filo
    titulo, _, _ = make_metadata(_cand(SOSA, CON_GANCHO, CIERRE))
    assert "error" in titulo.lower()


def test_dos_clips_del_mismo_video_no_repiten_titulo():
    cand = _cand(SOSA, CON_GANCHO, CIERRE)
    primero, _, _ = make_metadata(cand)
    segundo, _, _ = make_metadata(cand, {primero.lower()})
    assert segundo.lower() != primero.lower()


def test_el_titulo_no_corta_a_media_palabra():
    largo = ("El error que cometen absolutamente todas las personas que "
             "empiezan con esta herramienta y que nadie les explica nunca "
             "en ningun tutorial completo de internet es exactamente este.")
    titulo, _, _ = make_metadata(_cand(largo))
    assert len(titulo) <= 85
    assert titulo in largo or largo.startswith(titulo)


def test_la_descripcion_termina_en_frase_completa():
    _, descripcion, _ = make_metadata(_cand(SOSA, CON_GANCHO, CIERRE))
    assert descripcion.endswith(".")
    assert "…" not in descripcion


def test_los_hashtags_ignoran_genericos_y_muletillas():
    texto = ("En este video vamos a ver un clip sobre iluminacion. "
             "La iluminacion cambia el video entero, mira el clip. "
             "Iluminacion, siempre iluminacion en cada video y cada clip.")
    _, _, hashtags = make_metadata(_cand(texto))
    assert "iluminacion" in hashtags
    assert "video" not in hashtags and "clip" not in hashtags
