from celery import Celery
from app.config import settings

celery = Celery("switchboard", broker=settings.redis_url, backend=settings.redis_url)
celery.conf.update(task_track_started=True, task_serializer="json", accept_content=["json"], result_serializer="json")
