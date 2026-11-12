# Adaptadores

Visor separa dos cosas.

## Vista en vivo

go2rtc abre la URL RTSP que declares. Sirve para una cámara IP sola, para el canal de una cámara analógica que el grabador ya digitalizó, y para la cámara IP que el grabador republica. La página no habla con la cámara. Habla con go2rtc.

El catálogo (`cameras.json`) dice el nombre del stream, no la URL. La URL vive solo en `go2rtc.yaml`.

`kind` es `analog` o `ip`. Sirve para el informe de despliegue y para que quien despliega deje escrito qué tiene. La página no cambia el códec por ese campo.

- `analog`: canal del grabador que entra por coaxial o por la entrada de video. El visor no se conecta al cable. Usa el canal que el grabador expone.
- `ip`: cámara de red. Puede estar en el proxy del grabador (entonces tiene `channel` y hay archivo) o ser una URL RTSP suelta. Sin `channel` se ve en vivo y no entra en Grabación.

`dar` es la relación de aspecto de verdad, por ejemplo `[16, 9]`. No la pongas para estirar una cámara que no es panorámica. Sin `dar`, la página usa los píxeles del fotograma.

`stream_main` tiene que terminar en `_main`. La página pide el HD con el mismo nombre acabado en `_hd`. Ese HD es H.264 de rango limitado, una cámara a la vez. El mosaico y la tele se quedan en `stream_sub`.

## Grabaciones

Hoy el adaptador es ISAPI de Hikvision, en `app/cam_dashboard.py`. Cubre los canales analógicos y las cámaras IP que ese grabador indexa. No cubre Dahua, Uniview ni un NVR genérico. Decir lo contrario sería mentir.

Un segundo fabricante es un módulo nuevo con las mismas piezas:

- buscar segmentos de un canal entre dos horas
- resolver el archivo que contiene un instante (nombre, tamaño y fin de ese archivo)
- entregar la URL RTSP de reproducción de ese archivo, o el equivalente que go2rtc pueda leer
- activar o pausar la pista de grabación cuando el canal tiene imagen o la pierde

La página sigue llamando a `/api/recordings`, `/api/recordings/archive`, `/api/recordings/export` y `/api/recordings/play`. No hace falta duplicar la interfaz.

`VISOR_CHANNELS` es el máximo de canal que se consulta. El valor por defecto es 8. En un grabador de más canales se sube, hasta 64. Consultar canales vacíos alarga el archivo.

La escritura de pistas en este firmware de Hikvision no acepta el XML completo. La app cambia `Enable` y quita los campos de solo lectura. El usuario operador puede responder OK y no guardar; la escritura usa `admin` con la misma contraseña del entorno.

## Qué no es una nube de terceros

El video de archivo sigue en el disco del grabador. Visor no lo sube a Hik-Connect, EZVIZ ni a otro servicio. La descarga prepara un MP4 en el host y el navegador lo guarda en el equipo. Esa es la nube propia: tu grabador y tu servidor.

El primer fotograma HD puede salir gris un instante. go2rtc 1.9.9, al pedir solo video, antepone un filtro de demux. La plantilla `rtsp/hd` no usa `low_delay`; con HEVC de rango completo ese flag deja el gris fijo. La imagen se aclara en el fotograma siguiente.
