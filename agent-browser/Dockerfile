# Playwright base image — Chromium + its OS deps are already installed, so
# this image doesn't need `apt-get` dance. Pin to a known-good tag so the
# browser contract doesn't drift under us between deploys.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /srv

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# A non-root user already exists in the base image (`pwuser`).
USER pwuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+__import__('os').environ.get('PORT','8080')+'/health', timeout=3).status==200 else 1)"

CMD ["sh", "-c", "exec uvicorn app.service:app --host 0.0.0.0 --port \"$PORT\" --proxy-headers --forwarded-allow-ips='*'"]
