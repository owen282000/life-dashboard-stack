FROM python:3.14.8-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY receiver.py views.sql ./
COPY migrations ./migrations
EXPOSE 8080
CMD ["python", "receiver.py"]
