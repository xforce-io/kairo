FROM python:3.12-slim

RUN useradd --system --uid 65532 --create-home kairo

WORKDIR /tmp/build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[web]" && rm -rf /tmp/build

USER 65532:65532
WORKDIR /data
EXPOSE 8787
ENTRYPOINT ["kairo"]
CMD ["serve", "/data/current/data", "--mode", "public-read", "--host", "0.0.0.0", "--port", "8787"]
