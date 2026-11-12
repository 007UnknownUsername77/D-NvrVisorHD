# Seguridad

Un fallo que no deba verse en público se informa en la pestaña Security del repositorio. No abras un issue público con contraseñas, URLs RTSP ni un volcado de configuración.

## Qué no debe salir del equipo

- El archivo `.env`.
- `config/cameras.json` y `config/go2rtc.yaml` cuando ya tengan nombres reales o contraseñas.
- Cualquier URL RTSP que lleve usuario y contraseña.
- Claves privadas, tokens de un webhook y copias de video.

La aplicación no trae usuario ni contraseña por defecto. Si faltan `DVR_HOST`, `DVR_USER` o `DVR_PASS`, no arranca. `VISOR_DEMO=1` solo sirve para ver la página.

## Red

go2rtc no autentica su API. Quien llegue al puerto 1984 puede pedir video y, en algunos listados, ver la URL de origen con la contraseña. Por eso el compose publica 1984 y 8555 solo en 127.0.0.1, y no publica el RTSP 8554.

En un servidor sin Docker, `api.listen` y `webrtc.listen` tienen que ser `127.0.0.1` antes de arrancar. Para un teléfono de tu LAN, publica esos dos puertos en la dirección de la LAN. No los abras hacia internet y no des a la red de invitados una ruta hasta el grabador.

El navegador habla con go2rtc directo. Un proxy solo delante del puerto 8090 no tapa el 1984.

## Proceso

El unit de systemd corre con usuario de sistema, `NoNewPrivileges`, `PrivateTmp` y el sistema de archivos en solo lectura salvo el directorio de la página y `/var/backups`. El archivo de entorno queda `root:visor` modo 640.

Los errores que vuelven al navegador pasan por un filtro que quita `usuario:contraseña@` de la URL. Aun así, no reenvíes el log a un sitio público.

## Informe

`deploy_report.py` cuenta cámaras `analog` e `ip`. No lee el yaml de go2rtc y no incluye nombres de stream. El envío queda apagado hasta que existan `VISOR_REPORT_WEBHOOK`, `VISOR_REPORT_TOKEN` y `VISOR_REPORT_TO`.
