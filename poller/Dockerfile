FROM python:3.11-slim

LABEL maintainer="PyOutage"
LABEL description="Power outage poller — pings smart plug and logs to SQLite"

WORKDIR /app

# iputils-ping provides the ping binary
RUN apt-get update \
 && apt-get install -y --no-install-recommends iputils-ping \
 && rm -rf /var/lib/apt/lists/*

COPY poller.py .

CMD ["python", "-u", "poller.py"]
