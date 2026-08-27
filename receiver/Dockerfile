FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY receiver.py .
EXPOSE 8080
CMD ["python", "receiver.py"]
