FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir uv
COPY hse_geosensors.py .
RUN uv sync
COPY output ./output
CMD ["./.venv/bin/python", "-m", "flask", "--app", "hse_geosensors", "run", "--host=0.0.0.0"]
