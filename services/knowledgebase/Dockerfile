ARG PYTHON_IMAGE=python:3.12.12-slim-bookworm
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    KB_HOST=0.0.0.0 \
    KB_PORT=8767

WORKDIR /app
# This context is services/knowledgebase only: no Flocks Core source or deps.
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir . \
    && groupadd --gid 10001 knowledgebase \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp knowledgebase

USER 10001:10001
EXPOSE 8767
CMD ["python", "-m", "flocks_knowledgebase"]
