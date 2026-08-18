FROM python:3.13-slim
WORKDIR /app
COPY app.py ./
COPY static ./static
RUN mkdir -p /data && useradd -r -u 10001 app && chown -R app:app /data /app
USER app
ENV PORT=8080 DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=3)"
CMD ["python", "app.py"]
