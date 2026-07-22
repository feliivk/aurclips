# 🎬 aurclips — de videos largos a YouTube Shorts, 100% local

Convierte contenido largo (grabaciones propias, videos de YouTube, streams,
VODs) en **YouTube Shorts** verticales con subtítulos estilo viral, y los deja
**programados para publicarse solos, uno por día**. Toda la transcripción,
selección y edición corre en tu máquina, **gratis y sin API keys de pago**;
solo la subida automática a YouTube usa credenciales OAuth gratuitas de Google
(con cuota diaria).

> ### ⚠️ Estado: beta
>
> aurclips está en **desarrollo activo y fase de pruebas**. El pipeline funciona
> de punta a punta, pero los pesos del selector, la metadata y los defaults
> siguen en calibración: espera cambios de configuración entre versiones y
> **revisa lo que genera antes de publicarlo**. Por eso la subida automática
> viene desactivada (`upload.enabled: false`) y la revisión manual viene
> activada (`review.enabled: true`) de fábrica. Úsalo asumiendo que la calidad
> editorial la pones tú; la herramienta propone.

> Pensado para **Windows** (PowerShell + Programador de tareas). El código Python
> es portable, pero los scripts de instalación y automatización son de Windows.

## La idea: extremos apretados, centro simple

Cuando tú controlas la fuente, el problema deja de ser *"detectar buenos
momentos en footage desconocido"* y pasa a ser *"grabar de forma que extraer
sea fácil"*. aurclips está construido sobre esa idea, y se afina en tres puntos
del flujo — no parámetro a parámetro:

| | Dónde | Qué haces | Dónde vive |
| --- | --- | --- | --- |
| **1. Arriba** | Al grabar | Grabas en beats autocontenidos y **marcas** los buenos en vivo. Es la palanca más grande y no toca código. | [`docs/grabar-en-beats.md`](docs/grabar-en-beats.md) · `marks` |
| **2. Medio** | La selección | Calibras el selector a **tu género**, no al de otro. Charla tranquila y gaming no se puntúan igual. | `selection.profile` |
| **3. Abajo** | Título y datos | El LLM local redacta con el ángulo de tu canal, **tú apruebas**, y las métricas de lo publicado dicen hacia dónde ajustar. | `channel` · `titles` · `report` |

En el centro, el selector se queda **simple y honesto**: no modela arcos
narrativos ni persigue la viralidad a punta de heurística. Esa decisión está
registrada en [ADR-0001](docs/adr/0001-extremos-apretados-centro-simple.md).

## Cómo funciona el pipeline

```
Tu grabación (data\inbox) / canal de YouTube
        │  (marcas por voz o <video>.marks.txt)
        ▼
Transcripción local con Whisper (con tiempos por palabra)
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
Filtros de calidad:
  · anti-contenido no apto (términos es/en que desmonetizan)
  · limpieza de duplicados (similitud de transcripción entre clips)
        │
        ▼
ffmpeg recorta, aplica jump cuts (elimina pausas más largas que
`render.max_pause`), detecta el rostro y centra el encuadre vertical
en él, convierte a 9:16 (1080x1920) y quema subtítulos estilo viral
        │
        ▼
Revisas y apruebas (`aurclips review`)
        │
        ▼
Se suben a YouTube en privado con "publishAt" programado:
YouTube los publica solo, uno cada día a la hora que configures
```

## Requisitos

- **Windows 10/11** con PowerShell
- **[Python 3.12](https://www.python.org/downloads/)** (con el launcher `py`)
- Conexión a internet para el setup (~90 MB: ffmpeg, deno y la fuente Anton se
  descargan a `tools\`)
- **GPU NVIDIA opcional** — acelera mucho la transcripción; el setup la detecta
  e instala el soporte CUDA solo. En CPU también funciona (usa un modelo más chico).
- (Opcional, recomendado) **[Ollama](https://ollama.com)** para que un modelo
  local escriba los títulos
- (Solo para subir) una cuenta de Google y un proyecto gratuito en Google Cloud

## Instalación (una sola vez)

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Eso crea el entorno de Python, instala dependencias, descarga ffmpeg/deno/fuente
en `tools\` e instala soporte CUDA si detecta una GPU NVIDIA.

Después:

1. **Configura** `config.yaml`:
   - `channel.angle` y `channel.title_examples`: de qué va tu canal y 3-5
     títulos tuyos que te gusten. Es el contexto con el que se escriben los
     títulos; sin esto salen genéricos.
   - `selection.profile`: `comentario` (charla tranquila) o `gaming`.
   - `upload.publish_time`: hora local a la que quieres que salga el Short diario.
   - `whisper.model`: el default es `medium` (ideal con GPU NVIDIA); si solo
     tienes CPU, bájalo a `small`.
2. **(Recomendado) Ollama** — instala [Ollama](https://ollama.com) y baja el
   modelo que redacta:
   ```powershell
   ollama pull qwen2.5:7b
   ```
   aurclips lo detecta automáticamente. Sigue siendo local y gratis; sin Ollama
   la heurística escribe los títulos sola.
3. **Credenciales de YouTube** (solo para la subida automática):
   1. Entra a [Google Cloud Console](https://console.cloud.google.com/), crea un
      proyecto y habilita **YouTube Data API v3**.
   2. En *Credentials* crea un **OAuth client ID** de tipo **Desktop app** y
      descarga el JSON como `credentials\client_secrets.json`.
   3. En *OAuth consent screen* agrega tu cuenta como *test user*.
   4. Inicia sesión (abre el navegador una sola vez):
      ```powershell
      .venv\Scripts\python -m aurclips auth
      ```

> **La subida viene desactivada por defecto** (`upload.enabled: false`). Genera
> primero unos Shorts, revísalos en `data\output`, y cuando te convenzan cambia
> `upload.enabled: true` en `config.yaml`.

## Uso

```powershell
.venv\Scripts\python -m aurclips mark nombre  # marcar momentos mientras grabas
.venv\Scripts\python -m aurclips run          # pipeline completo
.venv\Scripts\python -m aurclips review       # aprobar/corregir títulos
.venv\Scripts\python -m aurclips status       # ver qué hay en cola
.venv\Scripts\python -m aurclips report       # métricas + qué está funcionando
.venv\Scripts\python -m aurclips retry        # reencolar videos/clips fallidos
.venv\Scripts\python -m aurclips ingest       # solo buscar contenido nuevo
.venv\Scripts\python -m aurclips process      # solo transcribir/recortar
.venv\Scripts\python -m aurclips upload       # solo subir lo aprobado
```

Suelta cualquier video largo en `data\inbox\` y corre `run`.

### Marcar lo que sí es un Short

Dilo en voz alta mientras grabas —**"esto es un short"**— y el selector prioriza
ese momento por encima de su propia puntuación. El segmento con la frase se
silencia, así que marca el clip pero no entra en él. No hace falta decirla
clavada (se compara por parecido) ni marcar todos los videos: uno sin marcas se
selecciona como siempre. Alternativa por timestamps:
un `<video>.marks.txt` al lado de la grabación, que puedes escribir con el
hotkey de tu grabadora o con `aurclips mark`. Guía completa:
[Grabar en beats](docs/grabar-en-beats.md).

### Revisar antes de publicar

`review` te muestra cada clip renderizado con su título, descripción y hashtags,
y te deja **aprobar (Enter), corregir el título, regenerarlo con el LLM o
descartarlo**. Nada se sube sin ese visto bueno mientras `review.enabled` sea
`true`. Son unos pocos clips al día: es el punto donde tu criterio entra al
pipeline sin tocar código.

### Afinar con datos, no con intuición

`report` trae vistas/likes de tus Shorts publicados y añade una sección **"Qué
está funcionando"**: vistas medias por duración del clip, por tipo de gancho del
título y según si lo marcaste tú o lo eligió el bot. Ajusta los pesos hacia
donde apunten esos números. Un resumen de tres líneas sale también como
cabecera de `review` —donde decides—, y solo con muestra suficiente: por debajo
de ~6 publicados no se muestra ninguna comparación, porque un promedio con n=3
sesga la decisión justo cuando más pesa. Además, la cola de subida da prioridad
a los clips de los videos fuente que mejor han rendido.

> **Cómo NO leerlo**: que "marcados por ti" rinda mejor no prueba que marcar
> mejore el rendimiento. Marcas los que ya te parecen buenos, así que la marca
> y la calidad salen del mismo sitio: tu criterio. Eso es selección, no
> causalidad — mide tu ojo, no el sistema de marcas.

**Alertas:** cada corrida escribe en `logs\events.log`. Si pones una URL de
webhook de Discord en `alerts.discord_webhook` (config.yaml), recibes un aviso
en tu servidor cuando se sube un Short o cuando algo falla.

## Configuración útil

Las claves de `config.yaml` que más vas a tocar (todas documentadas en el
propio archivo):

| Clave | Qué controla | Default |
| --- | --- | --- |
| `channel.angle` / `title_examples` | Contexto con el que se escriben los títulos | vacío |
| `marks.phrases` | Frases gatillo para marcar hablando | `esto es un short`, … |
| `marks.similarity` | Tolerancia al decir la frase (1.0 = literal) | `0.85` |
| `marks.exclusive` | Si hay marcas, ignorar el resto del video (un video sin marcas se selecciona normal) | `true` |
| `selection.profile` | Calibración del selector: `comentario` / `gaming` | `comentario` |
| `selection.weights` | Ajuste fino por señal sobre el perfil | `{}` |
| `selection.clips_per_video` | Tope máximo de Shorts por video | `3` |
| `selection.minutes_per_short` | Densidad: ~1 Short por cada N min de video | `4` |
| `selection.quality_floor` | Descarta candidatos bajo esa fracción del mejor (0 = off) | `0.55` |
| `titles.engine` / `titles.model` | Quién redacta los títulos | `auto` / `qwen2.5:7b` |
| `review.enabled` | Exigir tu aprobación antes de subir | `true` |
| `render.font_size` / `caption_position` | Tamaño y altura de los subtítulos | `112` / `0.70` |
| `render.max_pause` | Pausas más largas que esto (s) se recortan | `1.5` |
| `safety.action` / `safety.strict` | `skip`/`flag` y nivel del filtro | `skip` / `false` |
| `upload.publish_time` | Hora local de publicación diaria | `19:00` |
| `limits.max_videos_per_run` / `max_uploads_per_run` | Carga por corrida | `3` / `5` |

### Los pesos del selector

`selection.profile` fija una calibración base y `selection.weights` la ajusta.
Cada número es **lo máximo que esa señal mueve la puntuación**, así que se
comparan entre sí directamente:

| Señal | Qué mide | `comentario` | `gaming` |
| --- | --- | --- | --- |
| `energy` | Picos de audio | `0.12` | `0.30` |
| `pace` | Ritmo de habla vs. la mediana del video | `0.15` | `0.20` |
| `hook` | Palabras gancho en los primeros 8 s | `0.35` | `0.30` |
| `punct` | Preguntas y exclamaciones | `0.15` | `0.15` |
| `closes` | El clip termina cerrando la idea | `0.28` | `0.18` |
| `density` | Palabras con contenido, no relleno | `0.22` | `0.12` |
| `filler` | Penalización: arranca con muletilla | `0.15` | `0.12` |
| `gaps` | Penalización: silencios muertos | `0.40` | `0.40` |
| `mark` | Lo marcaste tú al grabar | `0.50` | `0.50` |

## Dejarlo trabajando solo

```powershell
powershell -ExecutionPolicy Bypass -File setup_task.ps1 -Hora "03:00"
```

Registra una tarea de Windows que corre el bot todos los días a las 3 AM:
busca contenido nuevo, genera los Shorts y los deja listos para tu revisión. La
publicación diaria la hace YouTube por sí solo (los videos se suben en privado
con fecha de publicación), así que aunque un día no haya contenido nuevo, los
Shorts ya encolados siguen saliendo. Cada corrida escribe su log en
`logs\run_<fecha>.log` (se conservan los últimos 30); los eventos importantes
van además a `logs\events.log`.

> Con `review.enabled: true` la corrida automática **no publica sola**: deja los
> clips esperando tu `review`. Si quieres el ciclo 100% desatendido, ponlo en
> `false` sabiendo lo que eso implica.

## Notas y límites

- **Estado beta**: los defaults van a cambiar mientras se calibra. Si tocas
  `selection.weights`, anota por qué —y comprueba contra `report`, no contra la
  intuición.
- **Calidad sobre volumen, y lo que eso implica**: `quality_floor` es relativo
  al mejor candidato *de cada video*, así que un video con un solo momento
  fuerte rinde **un** Short, no tres (medido en datos sintéticos: sobrevive 1
  de 11 candidatos, en ambos perfiles). Tus marcas quedan exentas, así que el
  volumen real depende de cuánto marques al grabar: **sin marcar, cuenta con
  ~1 Short por video fuente**. Es el primer número que hay que revisar con
  material propio —la distribución real puede abrirse o comprimirse distinto—
  y si el volumen se queda corto, el dial es `selection.quality_floor`, no los
  pesos.
- **Cuota de YouTube API**: cada subida cuesta ~1600 unidades de las 10,000
  diarias por defecto → máximo ~6 subidas al día. El bot programa las fechas de
  publicación en cadena, así que no necesitas subir más de unos pocos por corrida.
- **Publicación programada**: YouTube requiere que el video se suba como
  `private` con `publishAt`; el bot lo hace automáticamente.
- **Calidad de la selección**: sin marcas, la heurística funciona mejor con
  contenido hablado y con ideas que cierran. Con marcas, funciona con lo que
  tú decidas. Si publicas relleno, el problema casi nunca está en los pesos:
  está arriba, en cómo se grabó.
- **Encuadre**: con `crop.face_tracking` activado, el recorte vertical se
  centra en el rostro dominante detectado (OpenCV local); si no hay rostro,
  recorte centrado clásico.
- **Escalado**: `limits.max_videos_per_run` y `limits.max_uploads_per_run`
  controlan la carga por corrida; lo que no entra hoy queda en cola para la
  siguiente. Los estados son resumibles y `retry` reencola lo fallido.
- **Filtro de contenido**: `safety.action: skip` descarta los clips con
  lenguaje que desmonetiza; con `flag` los guarda marcados para que tú decidas
  (aparecen en `status`/`report`). Agrega términos propios en
  `safety.extra_words`.
- Los clips fallidos quedan marcados en la base (`data\state.db`); revisa con
  `status` y los logs en `logs\`.

## Desarrollo

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

Los tests corren en segundos, sin GPU, sin video real y sin Ollama.

## Licencia

[MIT](LICENSE). El modelo de detección de rostros embebido
([YuNet](https://github.com/opencv/opencv_zoo), int8) es también MIT.

Eres responsable de tener derechos sobre el contenido que recortas y de cumplir
los [términos de servicio de YouTube](https://www.youtube.com/t/terms) y las
políticas de la YouTube Data API al usar la subida automática.
