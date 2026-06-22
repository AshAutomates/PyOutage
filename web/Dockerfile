FROM python:3.11-slim

LABEL maintainer="PyOutage"
LABEL description="PyOutage dashboard — Flask web server"

WORKDIR /app

RUN pip install --no-cache-dir flask gunicorn

COPY app.py .
COPY templates/ templates/

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "60", "app:app"]
