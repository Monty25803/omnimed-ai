FROM python:3.12-slim
WORKDIR /srv
COPY lifesciences/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common /srv/common
COPY lifesciences/app /srv/app
COPY lifesciences/tests /srv/tests
ENV PYTHONPATH=/srv/common:/srv
RUN python -m pytest -q tests
EXPOSE 8083
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8083"]
