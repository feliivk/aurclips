# Extremos apretados, centro simple

El selector nació resolviendo el problema difícil: "detectar buenos momentos en
footage desconocido". Pero quien graba el material también es quien opera el
bot, así que el problema real es otro y es mucho más fácil: "grabar de forma
que extraer sea fácil". Decidimos no perseguir la viralidad con heurística
—es un fenómeno demasiado heterogéneo para eso— y mover el esfuerzo a los dos
extremos del flujo: **arriba**, las marcas del creador al grabar
(`marks.py`), que ganan a cualquier puntuación; **abajo**, la redacción del
título con contexto del canal (`titles.py`), la aprobación humana
(`aurclips review`) y la medición de lo publicado (`stats.py`). El centro
—`heuristics.py` y `select_clips.py`— se queda simple, auditable y calibrable
por perfil, sin modelar arcos narrativos.

## Consecuencias

- **El LLM local dejó de elegir clips.** Antes Ollama recibía candidatos
  truncados y escogía; ahora solo redacta, con la transcripción completa del
  clip. Un modelo de 7B no sabe qué le funciona a tu canal, y fingir que sí
  complicaba el centro sin mejorar la elección. La clave `selection.engine`
  desapareció; su reemplazo es `titles.engine`.
- **Las marcas ganan a la puntuación.** Con `marks.exclusive: true` (default),
  un video marcado ignora el resto de sus ventanas y las marcas quedan exentas
  del umbral de calidad. Es intencional: si marcaste, sabes algo que el audio
  y el texto no dicen.
- **La afinación va por datos, no por intuición.** Los pesos del selector se
  tocan mirando la sección "Qué está funcionando" de `aurclips report`, que
  compara lo publicado por duración, tipo de gancho y origen.
