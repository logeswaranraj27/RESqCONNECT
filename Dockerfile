FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV FLASK_ENV=production
ENV PORT=10000
EXPOSE 10000
CMD ["sh", "-c", "python -c \"from app import create_app; create_app()\" && python seed.py && exec gunicorn --config gunicorn.conf.py \"app:create_app()\""]
