FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./
COPY core ./core
COPY config ./config

RUN useradd --create-home --uid 1000 inspector
USER inspector

ENTRYPOINT ["python", "main.py"]
CMD ["--help"]
