# Grabar en beats (la palanca sin código)

Esta es la parte del sistema que no vive en el repositorio. Si grabas pensando
en que el material se va a cortar, el selector deja de tener que adivinar y la
mitad del problema desaparece antes de que corra una sola línea de Python.

## Por qué esto importa más que la configuración

Un selector automático no tiene el hilo narrativo: no sabe dónde empieza una
idea ni dónde termina. Puedes intentar enseñárselo con heurística —y aurclips
lo intenta— pero siempre va a ir por detrás de la alternativa obvia: **que la
idea venga ya completa desde la grabación**. Los creadores cuyo contenido se
clippea bien no tienen un algoritmo mejor; hablan de una forma que ya sale
recortable.

## Un beat

Un beat es una unidad autocontenida de 20-45 segundos con tres partes:

1. **Gancho** — una línea que se entiende sin contexto previo. Nada de "y como
   les decía", "siguiendo con esto". El espectador llega aquí desde cero.
2. **Punto** — la idea, el dato, la opinión. Una sola.
3. **Cierre** — la frase que la remata. El selector premia terminar la idea, y
   un clip que muere a media frase se siente roto aunque el momento sea bueno.

Entre beat y beat, deja un par de segundos de silencio. Le da un corte limpio
al recorte y te obliga a ti a cambiar de idea conscientemente.

## Marcar en vivo

Cuando sepas que acabas de dar un beat bueno, márcalo. Una marca tuya le gana a
cualquier heurística, y aurclips la respeta por encima de su propia puntuación.

**Por voz (lo más simple, no requiere nada):** di la frase gatillo en voz alta
justo antes del beat.

```
"Esto es un short." → [gancho] → [punto] → [cierre]
```

Whisper la transcribe, así que la marca cae exactamente donde la dijiste, sin
sincronizar relojes. Si el segmento es solo la frase, se silencia: marca el
clip pero no entra en él. Las frases se configuran en `marks.phrases`.

No hace falta decirla clavada: se comparan **por parecido** (`marks.similarity`,
0.85 por defecto), así que "esto es short" o un "shot" mal transcrito siguen
marcando. **Negar nunca marca**: *"esto no es un short"* se parece un 91% a la
frase gatillo y aun así se descarta, igual que *"nada de esto va para short"*
aunque contenga el gatillo literal. La regla es direccional —una negación
*antes* de la frase la anula, una *después* no—, así que *"esto es un short, no
te lo pierdas"* marca sin problema.

**Si un fraseo tuyo no marcó, agrégalo a `marks.phrases`; no bajes
`similarity`.** Es la diferencia entre arreglar el caso y abrir la puerta: una
variante nueva coincide al 100%, mientras que bajar el umbral acerca los falsos
positivos. Está medido: *"esto va a ser un short"* (legítima) daba 0.79 y *"no
todo lo que grabo es un short"* (trampa) 0.81 — están invertidas, así que no
hay umbral que las separe. Por eso la lista trae varias variantes de fábrica.

Cuando una marca entra por parecido y no literal, la corrida lo dice en
pantalla. Y si un video termina **sin ninguna marca**, se imprime lo que estuvo
cerca —con el número exacto que le faltó— o lo que se descartó por negación:
así el fallo se ve en vez de desaparecer. Para máxima precisión, usa un gatillo
corto y distinto de tu habla normal ("marca aquí") dicho como frase suelta.

**Por hotkey o archivo:** deja un `<video>.marks.txt` junto a la grabación, un
tiempo por línea (`12:34`, `1:02:03` o segundos sueltos). Sirve cualquier cosa
que escriba timestamps —el hotkey de tu grabadora, un bloc de notas— o el
comando incluido:

```bash
python -m aurclips mark mi-grabacion
```

Arráncalo en el mismo momento en que empiezas a grabar: cada Enter marca el
instante actual y al salir escribe `data/inbox/mi-grabacion.marks.txt`. Guarda
la grabación con el mismo nombre (`mi-grabacion.mp4`) en `data/inbox` y listo.

Con marcas en el video, `marks.exclusive: true` (el default) hace que solo
compitan esas ventanas: el resto del material se ignora. Si prefieres que la
heurística siga proponiendo por su cuenta, ponlo en `false` y las marcas pasan
a ser un empujón fuerte en vez de un filtro.

## Qué NO tienes que hacer

- **Guionizar.** Un beat es una idea con principio y final, no un texto leído.
- **Cambiar tu estilo.** Si hablas tranquilo, el perfil `comentario` de
  `selection.profile` ya está calibrado para eso: baja el peso del volumen y
  sube el del cierre de idea y la densidad de contenido.
- **Marcar todo.** Marca lo que de verdad publicarías. La lista de marcas es
  tu criterio editorial; si la inflas, deja de serlo.

## Cerrar el ciclo

Después de publicar, `python -m aurclips report` te dice qué tuvieron en común
los Shorts que rindieron: duración, tipo de gancho y si los marcaste tú o los
eligió el bot. Eso es lo que debe mover tus decisiones de grabación —y los
pesos del selector— no la intuición.
