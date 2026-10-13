# SCADAVT reproducibility image: runs the experiment suite and draws every
# figure and table of the paper.
#
#   docker build -t scadavt .
#   docker run --rm -v "$PWD/out:/out" scadavt
#
# The weekly consumption series is downloaded from Melbourne Water at run time
# (it is not redistributed with the code). Mount a folder on /app/data to reuse
# a copy you already have.
FROM python:3.12.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLBACKEND=Agg
RUN apt-get update && apt-get install -y --no-install-recommends fonts-urw-base35 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY scadavt ./scadavt
COPY tools ./tools
COPY tests ./tests
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps .
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /app/data /out
ENTRYPOINT ["entrypoint.sh"]
CMD ["all"]
