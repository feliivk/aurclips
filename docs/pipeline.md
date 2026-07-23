# Cómo funciona aurclips

aurclips tiene **un motor y dos niveles**. El motor —transcribir, seleccionar,
renderizar— es el mismo en los dos; lo que cambia es dónde termina el flujo:

- **Recortador** (`aurclips clip`): un archivo entra, salen los recortes con su
  metadata, y ahí acaba. Sin base, sin credenciales, sin cola.
- **Publicador** (`aurclips run`): el mismo motor, pero los clips entran a una
  base de estado, pasan por tu revisión y se suben a YouTube programados.

Por qué está partido así: [ADR-0002](adr/0002-recortador-en-la-puerta-publicador-dentro.md).

## La idea: extremos apretados, centro simple

Cuando tú controlas la fuente, el problema deja de ser *"detectar buenos
momentos en footage desconocido"* y pasa a ser *"grabar de forma que extraer
sea fácil"*. aurclips está construido sobre esa idea, y se afina en tres puntos
del flujo — no parámetro a parámetro:

| | Dónde | Qué haces | Dónde vive |
| --- | --- | --- | --- |
| **1. Arriba** | Al grabar | Grabas en beats autocontenidos y **marcas** los buenos en vivo. Es la palanca más grande y no toca código. | [Grabar en beats](grabar-en-beats.md) · `marks` |
| **2. Medio** | La selección | Calibras el selector a **tu género**, no al de otro. Charla tranquila y gaming no se puntúan igual. | [Selección](selection.md) · `selection.profile` |
| **3. Abajo** | Título y datos | El LLM local redacta con el ángulo de tu canal, **tú apruebas**, y las métricas de lo publicado dicen hacia dónde ajustar. | `channel` · `titles` · `report` |

En el centro, el selector se queda **simple y honesto**: no modela arcos
narrativos ni persigue la viralidad a punta de heurística. Esa decisión está
registrada en [ADR-0001](adr/0001-extremos-apretados-centro-simple.md).

## El flujo, de punta a punta

El tramo compartido —de la grabación al mp4 con subtítulos— es el mismo para
`clip` y para `run`. Al final el flujo se bifurca según el nivel:

```
Tu grabación (data\inbox) / canal de YouTube
        │  (marcas por voz o <video>.marks.txt)
        ▼
Transcripción local con Whisper (con tiempos por palabra)
  · en caché por hash del contenido: el mismo video no se
    retranscribe aunque lo renombres o lo muevas
        │
        ▼
Selector local de highlights:
  · tus marcas mandan: si señalaste el momento, ese es el clip
  · estructura narrativa: ganchos, preguntas, cierre de la idea,
    densidad de contenido, sin muletillas de arranque
  · energía del audio, con el peso que fije tu perfil
  → decide CUÁNTOS Shorts salen según duración y calidad del video
    (mejor pocos buenos que rellenar el cupo)
  → recorta cada clip para que termine en una frase completa
        │
        ▼
Título, descripción y hashtags:
  la transcripción COMPLETA del clip + el ángulo de tu canal + tus
  títulos de ejemplo → los redacta un LLM local (Ollama); sin Ollama,
  la heurística elige la frase con más gancho del clip
        │
        ▼
Filtros de calidad (safety.screen_clip, común a los dos niveles):
  · anti-contenido no apto (términos es/en que desmonetizan)
  · limpieza de duplicados (similitud de transcripción entre clips)
        │
        ▼
ffmpeg recorta, aplica jump cuts (elimina pausas más largas que
`render.max_pause`), detecta el rostro y centra el encuadre vertical
en él, convierte a 9:16 (1080x1920) y quema subtítulos estilo viral
        │
        ├─────────────── nivel 1: aurclips clip ───────────────
        │   Escribe cada recorte y su .txt (título, descripción,
        │   hashtags) en una carpeta por grabación. Fin: no toca
        │   la base ni consume hueco de publicación.
        │
        └─────────────── nivel 2: aurclips run ────────────────
            Cada clip entra a la base de estado
                    │
                    ▼
            Revisas y apruebas (`aurclips review`)
                    │
                    ▼
            Se suben a YouTube en privado con "publishAt"
            programado: YouTube los publica solo, uno cada día
            a la hora que configures
```

El nivel 2 en detalle —credenciales, cuota, programación y el ciclo de
despublicar— está en [Publicar en YouTube](upload-youtube.md).
