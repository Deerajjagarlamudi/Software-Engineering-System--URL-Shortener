FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
COPY scenarios ./scenarios
RUN pip install --no-cache-dir -e .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
