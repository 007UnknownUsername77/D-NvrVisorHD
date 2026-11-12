# Visor

Self-hosted viewer and recording browser for your own recorder. Live picture is any RTSP camera through go2rtc. Recording search and download use a Hikvision ISAPI adapter, for both analog channels and IP cameras on that recorder. Nothing is uploaded to a vendor cloud.

Visor es la aplicación para quien quiere ver y descargar sus cámaras en un servidor propio. No usa Hik-Connect, EZVIZ ni otra nube. El archivo de video sigue en el disco del grabador. El servidor solo prepara la vista y, cuando pides una copia, un MP4.

Hay dos clases de cámara, y las dos se declaran en el catálogo:

- **Analógica.** Entra al grabador por la entrada de video. Visor no habla con el cable. Usa el canal que el grabador ya digitalizó.
- **IP.** Cámara de red. Si el grabador la indexa, tiene canal y hay Grabación. Si solo tienes la URL RTSP, se ve en vivo y se deja `channel` fuera.

La vista en vivo vale para cualquier cámara que go2rtc pueda abrir. La Grabación, hoy, vale para un grabador Hikvision. Otra marca necesita el adaptador descrito en [docs/ADAPTERS.md](docs/ADAPTERS.md). No está escrita.

## Qué necesitas

- Un grabador o NVR en tu red, no expuesto a internet.
- go2rtc 1.9.9. La etiqueta está fijada a propósito. Esa versión es la de las plantillas de color del ejemplo.
- Python 3.12 y ffmpeg en el mismo equipo que la página, si no usas Docker.
- Un disco de vigilancia en el grabador si quieres días de archivo. Un SSD no adelanta la lectura cuando el límite es el puerto del grabador.

El valor `VISOR_CHANNELS` por defecto es 8. En un equipo de más canales se sube, hasta 64.

## Arranque con Docker

Desde la raíz de este repositorio:

```bash
cp .env.example .env
cp config/cameras.example.json config/cameras.json
cp config/go2rtc.yaml.example config/go2rtc.yaml
```

Edita `.env` con el nombre del grabador, el usuario y la contraseña. Edita `config/go2rtc.yaml` y cambia `USER`, `PASSWORD` y `RECORDER`. Esos dos archivos están en `.gitignore`.

En `config/cameras.json` deja `kind` en `analog` o `ip`. El nombre `stream_main` tiene que terminar en `_main`, y en go2rtc tiene que existir el mismo nombre acabado en `_hd`.

```bash
docker compose up -d --build
```

La página queda en `http://127.0.0.1:8090/`. El navegador de ese mismo equipo habla con go2rtc en el puerto 1984. Para un teléfono de tu LAN, cambia en `docker-compose.yml` el `127.0.0.1` de los puertos 8090, 1984 y 8555 por la dirección LAN del servidor. No pongas `0.0.0.0` en un equipo que tenga IP pública, y no publiques el 8554.

`VISOR_DEMO=1` abre la página aunque falte la contraseña. Las peticiones de archivo responden error. Sirve para mirar la interfaz, no para ver cámaras.

Dentro del contenedor la app escucha en todas las interfaces. El compose es quien recorta el host a loopback.

## Arranque en el servidor, sin Docker

```bash
sudo useradd --system --home /var/www/cam-viewer --shell /usr/sbin/nologin visor
sudo install -d -o visor -g visor -m 755 /var/www/cam-viewer /etc/visor /etc/go2rtc /var/backups
sudo install -o visor -g visor -m 644 app/index.html config/cameras.json /var/www/cam-viewer/
sudo install -o visor -g visor -m 644 app/cam_dashboard.py /usr/local/bin/cam_dashboard.py
sudo install -o root -g visor -m 640 .env /etc/visor/visor.env
sudo install -o root -g visor -m 640 config/go2rtc.yaml /etc/go2rtc/go2rtc.yaml
```

Antes de copiar el yaml, cambia `api.listen` y `webrtc.listen` a `127.0.0.1:1984` y `127.0.0.1:8555`. El 8554 ya está en localhost. Instala el binario de go2rtc 1.9.9 en `/usr/local/bin/go2rtc`.

```bash
sudo install -m 644 deploy/go2rtc.service deploy/visor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now go2rtc visor
```

El unit de la página carga `/etc/visor/visor.env` y ata el proceso a `127.0.0.1`. Hace falta `ffmpeg` en el `PATH` o en `/usr/local/bin`.

## Variables

| Variable | Uso |
| --- | --- |
| `DVR_HOST` | Nombre o dirección del grabador. Vacío, la app no arranca. |
| `DVR_USER` `DVR_PASS` | Cuenta del grabador. No van en el código. |
| `VISOR_CHANNELS` | Canales a consultar. Defecto 8, tope 64. |
| `VISOR_DEMO` | `1` muestra la página sin grabador. |
| `CAM_VIEWER_HOST` | `127.0.0.1` en el servidor. `0.0.0.0` solo dentro del contenedor. |
| `GO2RTC_URL` | API que usa el proceso Python. El navegador usa el host de la página, puerto 1984. |
| `GO2RTC_YAML` | De aquí se copia la URL al aparecer una cámara IP nueva. |
| `VISOR_REPORT_WEBHOOK` `VISOR_REPORT_TOKEN` `VISOR_REPORT_TO` | Informe opcional. Vacías, no se envía nada. |

## Informe de despliegue

```bash
python3 app/deploy_report.py
```

Imprime cuántas cámaras son analógicas, cuántas IP y cuántas no tienen `kind`. No imprime nombres de stream ni URLs. Si las tres variables del webhook tienen valor, hace `POST` de `{to, subject, text, html}` a `{webhook}/report` con `Authorization: Bearer`. Algunas pasarelas exigen un `User-Agent` concreto: se cambia con `VISOR_REPORT_USER_AGENT`.

## Cómo se ve

Una cámara a la vez puede ir en HD. Ese HD es H.264 de rango de televisión, porque el HEVC de rango completo se pinta gris en el navegador. El mosaico y la tele siguen en el substream, que pesa menos.

En el teléfono, Pantalla abre la vista grande dentro de la página. La pantalla completa nativa aplasta las cámaras altas. En el escritorio, Pantalla y el doble clic agrandan el cuadro, y el botón se queda dentro.

La grabación del iPhone va en HLS H.264, sin el micrófono: el audio G.711 frena la primera imagen. La descarga guarda el video tal como está en el disco. Si es H.265, la etiqueta queda en `hvc1` para que el teléfono lo abra. El audio de la descarga no va en el archivo.

El primer fotograma HD puede verse gris y aclararse enseguida. La plantilla de lectura no usa `low_delay`. Con ese flag el gris no se va.

## Desarrollo

```bash
make check
```

Compila Python, revisa el módulo de la página con `node --check` y corre el escaneo de secretos. El detalle para contribuir está en [CONTRIBUTING.md](CONTRIBUTING.md). El modelo de amenaza corto está en [SECURITY.md](SECURITY.md).

La licencia propuesta es MIT, con copyright «the Visor authors» hasta que quien publique ponga su nombre. El texto está en [LICENSE](LICENSE).
