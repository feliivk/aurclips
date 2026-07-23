# aurclips

Bot local que convierte grabaciones propias de gaming en Shorts de YouTube. El
creador graba y opera la herramienta, así que el vocabulario está escrito desde
ese punto de vista: el material es tuyo y las decisiones también.

Los `_Avoid_` valen para la prosa en español —comentarios, mensajes, docs—, no
para los identificadores, que van en inglés por convención del repo. Que el
glosario diga *Marca* no hace incorrecto a `marks.anchors`.

## Language

### Material

**Grabación**:
El video largo del que salen los clips: dejado en el inbox, descargado de un
canal, o bajado de una URL suelta. En el código y en la base es `video` (tabla
`videos`).
_Avoid_: fuente, input, video original

**Beat**:
Una unidad autocontenida de 20 a 45 segundos dentro de una grabación, con
gancho, punto y cierre. Grabar en beats es la palanca que hace innecesario que
el selector adivine.
_Avoid_: bloque, momento, sección

**Clip**:
Un fragmento continuo de una grabación, elegido para publicarse como Short.
_Avoid_: segmento, corte, highlight

**Candidata**:
Un tramo de la grabación que la heurística puntuó y que todavía no es un clip.
_Avoid_: candidato, propuesta

**Marca**:
Un momento que el creador señaló: por voz al grabar, por archivo/hotkey, o
repasando la grabación después. Manda sobre cualquier puntuación, venga de
donde venga.
_Avoid_: señal, tag

**Repaso**:
Ver la grabación para marcar momentos con una tecla; la marca cae en el
instante que está sonando. Sirve para material grabado sin marcar y para lo
descargado de canales. No confundir con **Revisión**: el repaso pasa antes de
seleccionar (le da tu criterio al selector), la revisión pasa después de
renderizar (tu criterio sobre clips ya hechos).
_Cómo_: `aurclips mark <ruta-del-video>`.
_Avoid_: re-watch, curación, replay

**Short**:
Un clip que ya está en YouTube, publicado o programado.
_Avoid_: publicación, video subido

**Recorte suelto**:
Un video vertical producido sin entrar al pipeline: sale del modo recortador,
no existe en la base y por eso no tiene ni progreso ni criterio. No aparece en
`status`, no espera revisión y no consume hueco de publicación. Es el mismo
material que un **Clip** —misma selección, mismo render— pero sin el ciclo de
vida que hace de un clip un **Short**.
_Avoid_: exportación, corte rápido

### Ciclo de vida

El estado de un clip son dos cosas independientes que no hay que confundir.

**Progreso**:
Hasta dónde llegó algo por la parte mecánica del pipeline. Un clip está
pendiente, renderizado, señalado, subido o fallido; una grabación está nueva,
transcrita, con clips elegidos, terminada, omitida o fallida. En la base es
`status`.
_Avoid_: estado (a secas), fase, etapa

**Criterio**:
El veredicto del creador sobre un clip: sin revisar, aprobado o descartado. Es
independiente del progreso — un clip puede estar renderizado y sin revisar, o
renderizado y descartado. En la base es `approved`.
_Avoid_: aprobación, validación, visto bueno

**Revisión**:
El momento en que el creador recorre los clips listos y aprueba, corrige o
descarta cada uno. Es donde entra su criterio al pipeline.
_Avoid_: moderación, QA, curación

**Señalado**:
Un clip que el filtro de contenido apartó por lo que dice, en vez de
descartarlo. No confundir con **Marca**: señalado lo decide el filtro, marcado
lo decides tú.
_Avoid_: marcado (para este sentido), flagueado

**Despublicar**:
Devolver a la cola de revisión un clip cuyo Short borraste tú de YouTube. El
sistema no borra nada del canal: registra que ya no está. Publicar no es el
final del camino.
_Cómo_: `State.clip_unpublished(clip_id)`, a propósito sin comando de CLI.
_Avoid_: revertir, deshacer, retirar

**Reencolar**:
Devolver al pipeline lo que falló, tan atrás como haga falta según qué
artefactos sobrevivieron. Es lo que hace el comando `retry`.
_Avoid_: reintentar, reprocesar

**Hueco de publicación**:
La ranura del calendario que ocupa un Short. Se reparte uno por día, y un hueco
ya consumido no se vuelve a ofrecer, ni siquiera al despublicar.
_Avoid_: agenda, turno

### Selección

**Piso de calidad**:
El valor por debajo del cual una candidata se descarta, medido como fracción de
la mejor candidata de esa misma grabación. Decide *cuántos* clips salen.
_Avoid_: mínimo, corte

**Perfil**:
El juego de pesos con que el selector puntúa, elegido según el género del canal.
Decide *cuál* clip sale.
_Avoid_: preset, modo, configuración
