FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    OTEL_SERVICE_NAME=rpa-wikipedia
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY rpa rpa
CMD ["python", "-m", "rpa"]
