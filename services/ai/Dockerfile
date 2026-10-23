FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY ai/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common /srv/common
COPY ai/app /srv/app
COPY ai/tests /srv/tests
ENV PYTHONPATH=/srv/common:/srv
RUN python -m pytest -q tests
EXPOSE 8084
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8084"]
