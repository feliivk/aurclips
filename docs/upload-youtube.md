# Publicar en YouTube

Esto es el **nivel 2** de aurclips: el bot sube los clips aprobados a tu canal
en privado con fecha de publicación, y YouTube los publica solo, uno por día.
Nada de esto hace falta para recortar — si solo quieres los videos, el modo
recortador no pide credenciales.

> **La subida viene desactivada de fábrica** (`upload.enabled: false`). Genera
> primero unos cuantos Shorts, míralos, y cuando te convenzan actívala.

## Credenciales (una sola vez)

1. Entra a [Google Cloud Console](https://console.cloud.google.com/), crea un
   proyecto y habilita **YouTube Data API v3**.
2. En *Credentials* crea un **OAuth client ID** de tipo **Desktop app** y
   descarga el JSON como `credentials/client_secrets.json`.
3. En *OAuth consent screen* agrega tu cuenta como *test user*.
4. Inicia sesión (abre el navegador una sola vez):

```bash
aurclips auth
```

Es gratis: la YouTube Data API no cobra, solo tiene cuota diaria.

## Revisar antes de publicar

```bash
aurclips review
```

Muestra cada clip renderizado con su título, descripción y hashtags, y te deja
**aprobar (Enter), corregir el título, regenerarlo con el LLM o descartarlo**.
Mientras `review.enabled` sea `true`, nada se sube sin pasar por ahí. Son unos
pocos clips al día: es el punto donde tu criterio entra al pipeline sin tocar
código.

## Cómo se programa la publicación

YouTube exige que un video con fecha programada se suba como `private` con
`publishAt`; el bot lo hace automáticamente. Reparte **un hueco de publicación
por día** a la hora de `upload.publish_time`, encadenando fechas: aunque un día
no haya contenido nuevo, los Shorts ya encolados siguen saliendo.

La cola da prioridad a los clips de las grabaciones que mejor han rendido.

## Cuota de la API

Cada subida cuesta ~1600 unidades de las 10 000 diarias por defecto → **máximo
~6 subidas al día**. Por eso `limits.max_uploads_per_run` viene en `5`: como las
fechas se programan en cadena, no necesitas subir más de unas pocas por corrida.

## Si un Short salió mal

Bórralo tú en YouTube Studio y devuelve su clip a la cola de revisión. El
sistema no borra nada de tu canal: solo registra que ya no está. Publicar no es
el final del camino.

No hay comando —es a propósito— pero sí una transición, con el `#id` que te
muestra `status`:

```bash
python -c "from aurclips.config import Config; from aurclips.state import State; State(Config().db_path).clip_unpublished(42)"
```

El hueco de publicación ya consumido no se devuelve: el siguiente Short se
programa igual para el día siguiente.

## Dejarlo trabajando solo

La corrida diaria es `aurclips run`: genera los Shorts y los deja listos para tu
revisión. Cada corrida escribe su log en `logs/run_<fecha>.log` (se conservan
los últimos 30) y se protege sola contra solapes; los eventos importantes van
además a `logs/events.log`.

Automatizarla es una receta por SO —todas están en
[`packaging/`](../packaging/README.md):

- **Linux**: un timer de systemd (o una línea de cron).
- **macOS**: un LaunchAgent de launchd (o cron).
- **Windows**: el Programador de tareas, con `setup_task.ps1 -Hora "03:00"`.

Si pones una URL de webhook de Discord en `alerts.discord_webhook`, recibes un
aviso cuando se sube un Short, cuando algo falla o cuando hay clips esperando tu
revisión.

> Con `review.enabled: true` la corrida automática **no publica sola**: deja los
> clips esperando tu `review`. Si quieres el ciclo 100% desatendido, ponlo en
> `false` sabiendo lo que eso implica: sin revisión la cola solo mira el
> progreso, así que también sube los clips que hubieras descartado antes.

## Responsabilidad

Eres responsable de tener derechos sobre el contenido que recortas y de cumplir
los [términos de servicio de YouTube](https://www.youtube.com/t/terms) y las
políticas de la YouTube Data API al usar la subida automática.
