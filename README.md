# 🎬 aurclips

Convierte videos largos en Shorts verticales con subtítulos, **completamente
local**. Corre en **Windows, Linux y macOS**.

```bash
aurclips clip mi_partida.mp4
```

```
data/output/mi_partida/
  0001_El_truco_que_nadie_conoce.mp4    ← 9:16, subtítulos quemados
  0001_El_truco_que_nadie_conoce.txt    ← título, descripción y hashtags
  0002_Por_que_nadie_termina_el_juego.mp4
  0002_Por_que_nadie_termina_el_juego.txt
```

Sin API keys, sin cuenta, sin mandar tu material a ningún servidor: transcribir,
elegir y editar pasa todo en tu máquina.

## Qué hace

- **Transcribe** con Whisper local, con tiempos por palabra (usa tu GPU NVIDIA
  si la tienes).
- **Elige los momentos**: lo que marcaste al grabar manda; si no marcaste,
  puntúa estructura (gancho, preguntas, cierre de idea, densidad) y energía de
  audio según tu género.
- **Recorta a 9:16**, quita las pausas muertas (jump cuts) y encuadra en el
  rostro si lo pides.
- **Quema subtítulos** estilo viral, palabra a palabra.
- **Escribe la metadata** de cada recorte en un `.txt` al lado: título,
  descripción y hashtags, listos para copiar y pegar. Con
  [Ollama](https://ollama.com) los redacta un modelo local; sin Ollama, una
  heurística.

## Qué NO hace

- **No adivina bien sin tu ayuda.** Sin marcar nada al grabar, cuenta con **~1
  recorte bueno por grabación**: el filtro de calidad prefiere quedarse corto
  antes que rellenar. Marcar cambia eso por completo.
- **No hay magia de IA en la selección.** Es una heurística simple y a propósito
  ([ADR-0001](docs/adr/0001-extremos-apretados-centro-simple.md)): no modela
  arcos narrativos ni persigue la viralidad. El criterio lo pones tú.
- **No sube nada.** Publicar en YouTube existe, pero es opcional y viene
  apagado.
- **No acelera en GPU en macOS.** La GPU NVIDIA acelera la transcripción en
  Windows y Linux; en macOS (incluido Apple Silicon) se transcribe en CPU. En
  CPU funciona en todas partes, solo más lento.
- **Está en beta.** Los defaults siguen en calibración: espera cambios de
  configuración entre versiones y **mira lo que genera antes de publicarlo**.

## Instalación

Necesitas **[Python 3.12](https://www.python.org/downloads/)** y **ffmpeg**. La
fuente de los subtítulos ya viene con el paquete.

**ffmpeg** (una vez, con tu gestor de paquetes):

| SO | Comando |
| --- | --- |
| macOS | `brew install ffmpeg` |
| Debian/Ubuntu | `sudo apt install ffmpeg` |
| Fedora | `sudo dnf install ffmpeg` |
| Windows | `winget install ffmpeg` (o lo descarga `setup.ps1` a `tools\`) |

**aurclips**:

```bash
# Linux / macOS
git clone https://github.com/Felii/aurclips && cd aurclips
sh setup.sh
```

```powershell
# Windows
git clone https://github.com/Felii/aurclips; cd aurclips
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Ambos crean un entorno virtual e instalan el comando `aurclips`. Si tienes GPU
NVIDIA (Windows/Linux), el setup detecta `nvidia-smi` y ofrece el soporte CUDA;
en CPU también funciona (baja `whisper.model` a `small`).

Opcional pero recomendado — un modelo local que escriba los títulos:

```bash
ollama pull qwen2.5:7b
```

aurclips lo detecta solo. Sigue siendo local y gratis.

> Los ejemplos usan el comando `aurclips` que crea el setup. Si prefieres no
> activar el entorno, es equivalente a `.venv/bin/python -m aurclips` (Linux/mac)
> o `.venv\Scripts\python -m aurclips` (Windows).

## Ejemplo

```bash
aurclips clip "~/grabaciones/partida 12.mkv"
aurclips clip partida.mp4 --out ~/edicion
aurclips clip partida.mp4 --clips 1
```

`--out` cambia la carpeta de destino y `--clips` pone un tope solo para esa
corrida. Nada de esto toca `config.yaml`, ni deja cola pendiente, ni necesita
credenciales: un recorte suelto entra y sale.

Recortar dos veces la misma grabación no la vuelve a transcribir — la
transcripción queda en caché, así que probar parámetros es barato. La segunda
corrida **reemplaza** los recortes de la primera en esa carpeta: si quieres
conservar los anteriores, dales otro `--out`.

Los mandos completos están en [Configuración](docs/config.md) y
[Selección](docs/selection.md).

## Luego: graba pensando en el recorte

Cuando tú controlas la fuente, el problema deja de ser *"detectar buenos
momentos en footage desconocido"* y pasa a ser *"grabar de forma que extraer sea
fácil"*. Es la palanca más grande que tienes y no toca código:

- **Graba en beats**: unidades de 20-45 s con gancho, punto y cierre.
- **Marca en vivo**: di **"esto es un short"** mientras grabas y ese momento
  gana sobre cualquier puntuación. El segmento con la frase se silencia, así que
  marca el clip pero no entra en él. No hace falta decirla clavada (se compara
  por parecido) ni marcar todos los videos.
- **O por timestamps**: un `<video>.marks.txt` al lado de la grabación, que
  puedes escribir con el hotkey de tu grabadora o con `aurclips mark`.

Guía completa: [Grabar en beats](docs/grabar-en-beats.md).

## Luego: que se publique solo

Si los recortes ya te convencen, aurclips también lleva el ciclo completo: sube
a YouTube en privado con fecha programada, y YouTube publica uno por día a la
hora que fijes.

```bash
aurclips run       # ingesta -> recortes -> subida
aurclips review    # aprobar o corregir antes de subir
aurclips status    # qué hay en cola
aurclips report    # métricas y qué está funcionando
aurclips retry     # reencolar lo que falló
```

A diferencia del modo recortador, esto sí lleva una base de estado: cada clip
tiene progreso (pendiente, renderizado, subido) y criterio tuyo (sin revisar,
aprobado, descartado). Mientras `review.enabled` sea `true`, nada se sube sin
pasar por tu criterio.

Para dejarlo corriendo solo cada día, hay una receta por SO —cron/systemd en
Linux, launchd en macOS, Programador de tareas en Windows— en
[`packaging/`](packaging/README.md).

Cómo dar de alta las credenciales, la cuota diaria, la programación y qué hacer
si un Short salió mal: [Publicar en YouTube](docs/upload-youtube.md).

Para vigilar canales y descargar material de YouTube en vez de usar tu propio
inbox, mira `channels` en [Configuración](docs/config.md).

## Documentación

| | |
| --- | --- |
| [Cómo funciona](docs/pipeline.md) | El motor y los dos niveles, con el flujo de punta a punta |
| [Grabar en beats](docs/grabar-en-beats.md) | Cómo grabar y marcar para que recortar sea trivial |
| [Selección](docs/selection.md) | Cuántos clips salen y cuáles: piso de calidad y pesos |
| [Configuración](docs/config.md) | Todas las claves de `config.yaml` |
| [Publicar en YouTube](docs/upload-youtube.md) | Credenciales, cuota, programación, despublicar |
| [CONTEXT.md](CONTEXT.md) | El vocabulario del proyecto |
| [ADR](docs/adr/) | Decisiones de arquitectura y por qué |

## Desarrollo

```bash
pip install -e .[dev]
pytest
```

Los tests corren en segundos, sin GPU, sin video real y sin Ollama.

## Licencia

[MIT](LICENSE). El modelo de detección de rostros embebido
([YuNet](https://github.com/opencv/opencv_zoo), int8) es también MIT.

Eres responsable de tener derechos sobre el contenido que recortas y de cumplir
los [términos de servicio de YouTube](https://www.youtube.com/t/terms) y las
políticas de la YouTube Data API al usar la subida automática.
