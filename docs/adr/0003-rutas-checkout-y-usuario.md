# Rutas: la base es la carpeta del config, no el paquete

Al portar aurclips a instalable (`pip`/`pipx`), las rutas dejaron de poder
colgar del paquete: instalado, `aurclips/` vive en `site-packages`, así que
derivar `config.yaml`, `data/` o `state.db` de `__file__` los pondría ahí —roto
en los tres SO—. Decidimos que la **base** contra la que se resuelven las rutas
relativas sea la **carpeta del `config.yaml` que se cargó**, descubierta en este
orden: `AURCLIPS_HOME` → el config del checkout (`ROOT/config.yaml`, si existe)
→ `./config.yaml` del directorio actual → la carpeta de usuario del SO
(platformdirs), sembrando ahí un config por defecto en el primer arranque.

El orden es deliberado y **el fallback a `ROOT/config.yaml` no es opcional**: es
lo que hace que quien corre desde el checkout —el caso del creador— siga usando
`./config.yaml` y `./data` exactamente como antes del port, con su `state.db`
intacto. Un test lo fija (`test_un_config_explicito_resuelve_relativos...`).

## Consecuencias

- **Dos modos coexisten a propósito.** Checkout (base = raíz del repo) e
  instalado (base = dir de datos de usuario). Quitar el escalón del checkout
  para "simplificar" a un solo modelo movería el `state.db` del creador sin
  avisar — justo lo que este ADR previene.
- **Las rutas absolutas en `paths.*` se respetan tal cual**, en cualquier modo.
  De eso dependen los tests, que apuntan a carpetas temporales.
- **El config vive en dos sitios** (la raíz del repo y el empaquetado
  `aurclips/assets/config.default.yaml`, que se siembra al instalar). Un test
  los obliga a ser idénticos, como el de los defaults de render.
- **`AURCLIPS_HOME`** da una salida explícita para quien quiera fijar la base a
  mano (contenedores, varias instancias) sin depender del descubrimiento.
- Los binarios (ffmpeg) siguen la misma idea de "base + raíz del repo + PATH":
  ver [`_tool`](../../aurclips/config.py). Es coherente con
  [ADR-0002](0002-recortador-en-la-puerta-publicador-dentro.md): un solo motor,
  dos formas de invocarlo.
