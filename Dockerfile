FROM python:3.12.9-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml requirements-web.lock ./
COPY client_intel ./client_intel
COPY eval/requests.json ./eval/requests.json
RUN python -m pip install --no-cache-dir -r requirements-web.lock \
    && python -m pip install --no-cache-dir --no-deps . \
    && groupadd --gid 10001 clientintel \
    && useradd --uid 10001 --gid 10001 --no-create-home clientintel \
    && mkdir -p /data /outputs /state /app/config \
    && chown 10001:10001 /outputs /state

USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python", "-m", "client_intel"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
