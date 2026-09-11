FROM python:3.11-slim

# Needed for `pg_isready` if a future entrypoint wants to wait on Postgres
# itself; harmless and tiny either way. psycopg2-binary (in requirements.txt)
# needs no build toolchain, so no compiler packages are required here.
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Deterministic - regenerating this at build time (rather than loading it
# into a database) needs no DB connection, so it's safe to do here. The
# actual database load (SQLite or Postgres, whichever DATABASE_URL points
# to at *runtime*) happens when the container starts, via the db-init
# service in docker-compose.yml (`python3 -m app.database.load_data`) -
# not here, since a Postgres service isn't reachable during `docker build`.
RUN cd data && python3 generate_synthetic_data.py

EXPOSE 8000

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
