FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bandit_pipeline/ ./bandit_pipeline/

ENV PYTHONUNBUFFERED=1
ENV DAGSTER_HOME=/tmp/dagster
ENV DAGSTER_MODULE_NAME=bandit_pipeline.definitions

EXPOSE 4000

CMD ["dagster", "code-server", "start", "--host", "0.0.0.0", "--port", "4000", "--module-name", "bandit_pipeline.definitions"]
