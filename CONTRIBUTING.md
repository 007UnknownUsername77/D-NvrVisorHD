# Contribuir

El idioma de la interfaz y de los textos de usuario es español. Los comentarios nuevos, una línea, solo cuando el motivo no se ve en el código.

## Antes del pull request

Desde la raíz del repositorio:

```bash
make check
```

Eso compila los dos Python, revisa la sintaxis del módulo de `app/index.html` y busca direcciones privadas, credenciales RTSP y claves privadas. El informe de ejemplo tiene que imprimir una analógica y una IP, y decir que el correo está apagado.

No incluyas `.env`, `config/cameras.json`, `config/go2rtc.yaml`, video ni capturas.

## Adaptadores

La vista en vivo es go2rtc. La búsqueda y la descarga de grabaciones están en `app/cam_dashboard.py` y hoy hablan ISAPI de Hikvision. Otra marca es un módulo con las mismas funciones de búsqueda y reproducción, no un cambio repartido por la página. El contrato está en `docs/ADAPTERS.md`.

## Qué no cambia a la ligera

- El HD de una sola cámara usa el stream que termina en `_hd` (H.264, rango de TV). El mosaico y la tele siguen en el sub.
- La reproducción de una grabación es un solo productor. La app borra `dvr_playback` y luego lo crea. No añadas un `ffmpeg:` que apunte a ese mismo nombre.
- En el iPhone la grabación va en HLS H.264 y sin el audio G.711. Si se adjunta, la primera imagen se queda parada.
- La descarga es el video almacenado, copiado, con la etiqueta `hvc1` cuando el códec es H.265. El audio G.711 no entra en el MP4.
- `go2rtc` se queda en 1.9.9 mientras las plantillas `h264/phone`, `rtsp/hd` y `rtsp/play` sean las del ejemplo. Subir la imagen sin mirar un fotograma real puede devolver el gris del HEVC de rango completo.
