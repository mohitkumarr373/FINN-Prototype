FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m finn && mkdir -p /app/data && chown -R finn:finn /app
USER finn
EXPOSE 8000
CMD ["uvicorn", "finn.app:app", "--host", "0.0.0.0", "--port", "8000"]
