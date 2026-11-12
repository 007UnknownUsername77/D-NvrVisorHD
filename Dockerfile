FROM python:3.12-slim-bookworm

# ffmpeg prepares a download. The image ships no credentials and no real catalog.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/visor
COPY app/cam_dashboard.py app/index.html app/deploy_report.py app/icon.png /opt/visor/
COPY config/cameras.example.json /opt/visor/cameras.json
RUN chmod -R a+rX /opt/visor

USER nobody
ENV CAM_VIEWER_HOST=0.0.0.0 \
    VISOR_HOST=0.0.0.0 \
    CAM_VIEWER_ROOT=/opt/visor \
    CAM_VIEWER_PORT=8090 \
    GO2RTC_URL=http://go2rtc:1984 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/api/health', timeout=3)"]
CMD ["python3", "/opt/visor/cam_dashboard.py"]
