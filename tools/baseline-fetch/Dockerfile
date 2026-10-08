FROM python:3.12-slim-bookworm
RUN pip install --no-cache-dir boto3==1.40.* 2>/dev/null || pip install --no-cache-dir boto3
COPY fetch.py /app/fetch.py
USER 1000
ENTRYPOINT ["python", "/app/fetch.py"]
