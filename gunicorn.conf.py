import os

bind = "0.0.0.0:5000"
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
worker_class = "sync"
timeout = 30
graceful_timeout = 20
keepalive = 2
accesslog = "-"
errorlog = "-"
capture_output = True