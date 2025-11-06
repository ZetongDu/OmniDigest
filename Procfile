web: uvicorn src.omnidigest.api.app:app --host 0.0.0.0 --port $PORT
worker: python -m src.omnidigest.delivery.schedule_worker