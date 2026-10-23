FROM python:3.12-slim
WORKDIR /srv
COPY diagnostics/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common /srv/common
COPY diagnostics/app /srv/app
COPY diagnostics/tests /srv/tests
ENV PYTHONPATH=/srv/common:/srv
RUN python -m pytest -q tests
EXPOSE 8082
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8082"]
