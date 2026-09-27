FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/francoisgn/seed-box" \
      org.opencontainers.image.licenses="LicenseRef-PolyForm-Strict-1.0.0" \
      org.opencontainers.image.description="Seeding coverage dashboard: library vs qBittorrent vs Prowlarr"

# Config: /config/seedbox.toml if mounted, and/or SEEDBOX_* variables.
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 SEEDBOX_OUTPUT_DIR=/data

WORKDIR /app
COPY LICENSE ./
COPY seedbox/ seedbox/
RUN mkdir -p /data && chown 1000:1000 /data

# Default user; override with `user:` in compose so the media stays readable.
USER 1000:1000
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=5m --timeout=5s --start-period=30s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/' % os.environ.get('SEEDBOX_PORT', '8080'), timeout=4)"

ENTRYPOINT ["python", "-m", "seedbox"]
CMD ["run"]
