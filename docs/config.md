# Configuración

Todas las claves de `config.yaml`, con el valor que traen de fábrica. El propio
archivo está comentado; esto es la referencia para consultar de un vistazo.

> Estado beta: los defaults van a cambiar mientras se calibra. Si tocas algo,
> anota por qué — y comprueba contra `report`, no contra la intuición.

## Tus marcas al grabar — `marks`

Lo que señalas mientras grabas manda sobre cualquier puntuación. Guía completa:
[Grabar en beats](grabar-en-beats.md).

| Clave | Qué controla | Default |
| --- | --- | --- |
| `marks.enabled` | Usar marcas por voz y por archivo | `true` |
| `marks.phrases` | Frases gatillo. ¿Un fraseo tuyo no marcó? Agrégalo aquí — es más seguro que bajar `similarity` | `esto es un short`, … |
| `marks.similarity` | Tolerancia al decir la frase (`1.0` = literal). Negar nunca marca: "esto NO es un short" se descarta | `0.85` |
| `marks.exclusive` | Si el video trae marcas, solo se miran esas ventanas. Un video **sin** marcas se selecciona normal | `true` |
| `marks.tolerance` | Holgura (s) al emparejar una marca con una ventana | `3.0` |

## Tu canal — `channel`

El contexto con el que se escriben los títulos. Sin esto salen genéricos.

| Clave | Qué controla | Default |
| --- | --- | --- |
| `channel.angle` | De qué va tu canal, en una línea | vacío |
| `channel.title_examples` | 3-5 títulos tuyos que te gusten: marcan la línea editorial a imitar | vacío |

## Contenido de entrada

| Clave | Qué controla | Default |
| --- | --- | --- |
| `channels` | Canales de YouTube a vigilar (URL del canal o de su pestaña `/videos`) | vacío |
| `channel_scan_limit` | Videos recientes que se revisan por canal en cada corrida | `5` |
| `min_source_duration` | Duración mínima (s) para descargar un video de un canal. No aplica al inbox | `300` |
| `max_download_height` | Calidad máxima de descarga | `1080` |

## Transcripción — `whisper`

| Clave | Qué controla | Default |
| --- | --- | --- |
| `whisper.model` | `tiny`/`base`/`small`/`medium`/`large-v3`. Con GPU NVIDIA, `medium` o `large-v3`; solo CPU, baja a `small` | `medium` |
| `whisper.language` | `null` = autodetectar; o `es`, `en`, … | `null` |
| `whisper.device` | `auto` = GPU si hay, con respaldo automático a CPU | `auto` |
| `whisper.compute_type` | Precisión; `auto` elige según el dispositivo | `auto` |

Cambiar `model` o `language` invalida la caché de transcripciones: lo ya
transcrito con otro modelo no se reutiliza.

## Selección — `selection`

Los pesos y el piso de calidad tienen su propia página:
[Selección](selection.md).

| Clave | Qué controla | Default |
| --- | --- | --- |
| `selection.profile` | Calibración del selector: `comentario` / `gaming` | `comentario` |
| `selection.weights` | Ajuste fino por señal sobre el perfil | `{}` |
| `selection.clips_per_video` | Tope máximo de Shorts por video | `3` |
| `selection.minutes_per_short` | Densidad: ~1 Short por cada N min (`0` = usar siempre el tope) | `4` |
| `selection.quality_floor` | Descarta candidatas bajo esa fracción de la mejor (`0` = off) | `0.55` |
| `selection.min_clip_seconds` | Duración mínima de un clip | `15` |
| `selection.max_clip_seconds` | Duración máxima de un clip | `59` |

## Títulos y revisión — `titles`, `review`

| Clave | Qué controla | Default |
| --- | --- | --- |
| `titles.engine` | `heuristic` (sin LLM) / `ollama` (siempre) / `auto` (Ollama si está corriendo) | `auto` |
| `titles.model` | Modelo de Ollama que redacta. Alternativa ligera: `gemma3:4b` | `qwen2.5:7b` |
| `titles.url` | Endpoint de Ollama | `http://localhost:11434` |
| `review.enabled` | Pasar por tu criterio antes de subir | `true` |

## Render — `render`, `crop`

| Clave | Qué controla | Default |
| --- | --- | --- |
| `render.subtitles` | Quemar subtítulos virales | `true` |
| `render.words_per_caption` | Palabras por frase en pantalla | `3` |
| `render.font` | Fuente; se descarga a `tools/fonts` | `Anton` |
| `render.font_size` | Sobre lienzo 1080x1920. El ancho útil son 940 px (márgenes de 70): con 3 palabras por frase, ~160 es el techo cómodo. Más arriba, baja `words_per_caption` a 2 | `160` |
| `render.outline` | Grosor del contorno negro. Escala con el tamaño (~9%), o la letra se come el borde | `14` |
| `render.base_color` | Color del texto | `#FFFFFF` |
| `render.highlight_colors` | Palabra clave resaltada (rota entre estos) | amarillo/verde |
| `render.caption_position` | Altura del texto (`0.70` = tercio bajo, libre de la UI de Shorts) | `0.70` |
| `render.tighten_silences` | Jump cuts: recortar pausas muertas | `true` |
| `render.max_pause` | Pausas más largas que esto (s) se recortan. `1.0` para charla pura; `1.5-2.0` en gaming conserva acción | `1.5` |
| `render.crf` / `render.preset` | Calidad y velocidad de codificación | `20` / `veryfast` |
| `crop.face_tracking` | Centra el recorte vertical en el rostro dominante. Viene apagado porque en gameplay sin cámara el encuadre perseguiría caras de personajes; ponlo en `true` solo si grabas cara a cámara | `false` |

## Filtro de contenido y duplicados — `safety`, `dedup`

| Clave | Qué controla | Default |
| --- | --- | --- |
| `safety.enabled` | Revisar la transcripción de cada clip | `true` |
| `safety.action` | `skip` = descartar el clip \| `flag` = guardarlo señalado para que decidas | `skip` |
| `safety.strict` | `true` añade groserías comunes (canales de marca/infantiles); `false` las permite | `false` |
| `safety.extra_words` | Términos propios a bloquear, además de la lista integrada | vacío |
| `dedup.enabled` | Descartar clips casi idénticos | `true` |
| `dedup.similarity` | Umbral de similitud (0-1) para considerar duplicado | `0.8` |

En el pipeline, un clip se compara contra todos los de la base. En el modo
recortador, solo contra los recortes de la misma corrida: no hay base que
recuerde las anteriores.

## Límites, métricas y alertas

| Clave | Qué controla | Default |
| --- | --- | --- |
| `limits.max_videos_per_run` | Videos largos procesados por corrida | `3` |
| `limits.max_uploads_per_run` | Subidas por corrida (la cuota da ~6/día) | `5` |
| `stats.enabled` | Consultar vistas/likes de los Shorts publicados | `true` |
| `alerts.discord_webhook` | URL de webhook de Discord para avisos | `null` |
| `alerts.notify_on` | Eventos que avisan. `review` = hay clips esperando tu revisión | `error`, `uploaded`, `review` |

## Publicación — `upload`

Detalle completo en [Publicar en YouTube](upload-youtube.md).

| Clave | Qué controla | Default |
| --- | --- | --- |
| `upload.enabled` | Subir a YouTube. **Viene apagado** | `false` |
| `upload.privacy` | Se sube en privado y YouTube lo publica solo a la hora fijada | `private` |
| `upload.publish_time` | Hora local de publicación diaria | `19:00` |
| `upload.timezone` | `null` = zona del sistema; o `America/Mexico_City`, … | `null` |
| `upload.min_lead_hours` | Margen mínimo entre subir y publicar | `1` |
| `upload.category_id` | `24` Entertainment, `20` Gaming, `22` People & Blogs | `24` |
| `upload.extra_tags` | Tags añadidos a todos los Shorts | `["shorts"]` |

## Rutas — `paths`

Relativas a la raíz del proyecto.

| Clave | Default |
| --- | --- |
| `paths.data` | `data` |
| `paths.inbox` | `data/inbox` — deja aquí videos locales para procesar |
| `paths.downloads` | `data/downloads` |
| `paths.work` | `data/work` — transcripciones en caché y archivos intermedios |
| `paths.output` | `data/output` |
| `paths.credentials` | `credentials` — `client_secrets.json` y token de YouTube |
| `paths.ffmpeg` | `tools/ffmpeg/bin` — carpeta con `ffmpeg.exe` y `ffprobe.exe` |
