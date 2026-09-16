# Precio de la luz — web para el móvil (gratis)

Cada noche GitHub Actions consulta el PVPC en ESIOS, calcula la mejor hora para cada
electrodoméstico y publica `docs/data.json`. La web (`docs/index.html`) lo muestra.

## Puesta en marcha (10 minutos)

1. Pide tu token gratuito de ESIOS a consultasios@ree.es.
2. Crea un repositorio en GitHub (público: Actions ilimitadas) y sube estos archivos
   respetando las carpetas `.github/workflows/` y `docs/`.
3. Settings → Secrets and variables → Actions → New repository secret:
   nombre `ESIOS_TOKEN`, valor tu token.
4. Settings → Pages → Source: "Deploy from a branch", rama `main`, carpeta `/docs`.
5. Actions → "PVPC diario" → Run workflow (primera ejecución manual).
6. Abre `https://TUUSUARIO.github.io/NOMBRE-REPO/` en el móvil y añádela a la
   pantalla de inicio (Safari: Compartir → Añadir a pantalla de inicio; Chrome: menú → Añadir a pantalla de inicio).

## Ajustar a tu rutina

Edita la lista `APPLIANCES` en `fetch_pvpc.py` (duración, horas permitidas, dependencias).
El siguiente run de la Action regenera los datos.

## Notas

- Los crons de GitHub pueden retrasarse varios minutos; hay tres pasadas al día por si acaso.
- Los datos son de Península (Galicia incluida), sin IVA, tal como los publica Red Eléctrica.
