# syntax=docker/dockerfile:1

# There is nothing to build. The app is one stdlib-only Python file, so the
# image is just the runtime plus the source — no pip, no wheels, no compiler.
FROM python:3.13-alpine

LABEL org.opencontainers.image.title="Bijou" \
      org.opencontainers.image.description="Illuminated Plex movie poster display" \
      org.opencontainers.image.licenses="MIT"

RUN adduser -D -H -u 10001 bijou

WORKDIR /app
COPY app/ /app/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BIJOU_BIND=0.0.0.0 \
    BIJOU_PORT=8080 \
    BIJOU_STATIC=/app/static

USER bijou
EXPOSE 8080

# The app already polls Plex on a timer; healthz reports whether that's working.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import urllib.request,sys; \
      sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=4).status==200 else 1)"

ENTRYPOINT ["python3", "/app/bijou.py"]
