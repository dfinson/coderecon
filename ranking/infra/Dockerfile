FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl && \
    curl -sL https://aka.ms/InstallAzureCLIDeb | bash && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install codeplane from the repo (editable for now — swap to wheel for prod)
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir ".[dev]" 2>/dev/null || pip install --no-cache-dir .

COPY ranking/infra/index_job.sh /index_job.sh
RUN chmod +x /index_job.sh

WORKDIR /work
ENTRYPOINT ["/index_job.sh"]
