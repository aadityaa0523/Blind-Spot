FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV CLONE_DIR=/data/clones PR_CACHE=/data/pr_cache.json PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os,urllib.request as u; u.urlopen('http://localhost:%s/api/health' % os.environ.get('PORT','8000'))" || exit 1
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
