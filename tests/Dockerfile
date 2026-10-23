FROM python:3.12-slim
WORKDIR /tests
# curl is the native-client baseline for large transfers; httpx tops out near 120 MiB/s per process.
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
ENV PYTHONUNBUFFERED=1
CMD ["pytest", "-ra", "-p", "no:cacheprovider"]
