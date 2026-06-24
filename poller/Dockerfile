FROM python:3.11-slim

LABEL maintainer="PyOutage"
LABEL description="PyOutage poller — event-based power cut logger"

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends iputils-ping \
 && rm -rf /var/lib/apt/lists/*

COPY poller.py .
COPY config.json .

CMD ["python", "-u", "poller.py"]
