# Recortador en la puerta, publicador dentro

aurclips empezó siendo un publicador automático de YouTube, pero eso hacía que
la primera corrida dependiera de trámites (credenciales de Google, base de
estado, revisión) que no hacen falta para la pregunta que trae quien llega:
*¿me gustan los recortes que produce?*. Decidimos partir el producto en dos
niveles sobre el **mismo motor**: un **recortador** en la puerta (`aurclips
clip`) que transcribe, selecciona y renderiza un archivo y para ahí —sin base,
sin credenciales, sin cola—, y el **publicador** de siempre (`aurclips run` y
su ciclo) para quien ya confía en los recortes y quiere que se suban solos. El
recortador produce **recortes sueltos** (ver [CONTEXT.md](../../CONTEXT.md)):
clips sin progreso ni criterio, que no existen en la base ni consumen hueco de
publicación.

Se apoya en [ADR-0001](0001-extremos-apretados-centro-simple.md): como el
centro es simple y sin estado, envolverlo en un segundo punto de entrada es
barato y no obliga a un pipeline paralelo.

## Consecuencias

- **El motor es uno solo, y tiene que seguir siéndolo.** Transcripción,
  selección, filtro de contenido y render los comparten los dos niveles. La
  política de descarte vive en una sola función (`safety.screen_clip`)
  precisamente para que no pueda divergir: la primera versión del recortador
  copió ese bucle y ya se había separado del pipeline (perdía el estado
  `flagged`) cuando la revisión lo detectó. Cualquier regla nueva de descarte
  se añade ahí, no en cada modo.
- **`clip` no abre la base.** Es el único comando que se carga con la config a
  secas, sin `State`. Es una invariante, no un detalle: en cuanto un recorte
  suelto tocara la base tendría progreso y criterio, y dejaría de ser suelto.
- **El recortador reutiliza la caché de transcripciones, no un atajo propio.**
  Recortar el mismo video muchas veces mientras se ajustan parámetros es el
  caso de uso, y es barato porque Whisper solo corre la primera vez
  ([ADR-0001](0001-extremos-apretados-centro-simple.md) dejó el centro sin
  estado; la caché por hash lo aprovecha).
- **Los recortes sueltos no se mezclan con los del pipeline.** Van a una
  carpeta por grabación y se numeran por posición en la corrida; los del
  pipeline se numeran por id de clip. Apuntar `--out` a la carpeta del pipeline
  se rechaza, porque las dos numeraciones chocarían.
- **La portada vende el nivel 1.** El README abre con el recortador y presenta
  la publicación como un segundo nivel opcional. Esto es reversible en el texto,
  pero la decisión de fondo —qué es aurclips de un vistazo— no debería
  revertirse sin querer al reordenar docs.
- **Ascender un recorte suelto a la cola de publicación queda fuera.** Si un
  recorte gusta, se sube a mano con su `.txt`. El puente automático entre los
  dos niveles sería otra decisión, no un efecto secundario de ésta.
