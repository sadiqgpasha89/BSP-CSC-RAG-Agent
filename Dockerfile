FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Outside development the app refuses to start without SECRET_KEY and ADMIN_API_KEY (see .env.example).
ENV ENVIRONMENT=production
EXPOSE 8000
USER 1000
CMD ["python", "-m", "uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
