# Stage 1: Builder — install Python dependencies
FROM python:3.12-slim AS builder
WORKDIR /app
COPY filter_app/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Runtime — minimal production image
FROM python:3.12-slim
WORKDIR /app

# Create non-root user
RUN groupadd -r streamlit && useradd -r -g streamlit -m -u 1000 streamlit

# Copy installed packages from builder
COPY --from=builder /root/.local /home/streamlit/.local

# Copy only what's needed at runtime
COPY filter_app/ ./filter_app/
COPY data/ ./data/

# Set ownership
RUN chown -R streamlit:streamlit /app
USER streamlit

# Ensure pip-installed binaries are on PATH
ENV PATH=/home/streamlit/.local/bin:$PATH

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

CMD ["streamlit", "run", "filter_app/streamlit_app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
