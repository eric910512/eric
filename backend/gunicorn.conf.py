"""Gunicorn settings for hosted deployments (Render). Values can be tuned with env vars."""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"  # Render provides PORT
worker_class = "gthread"
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))
graceful_timeout = 30
keepalive = 5
# Render terminates TLS at its proxy; trust its X-Forwarded-* headers (the app applies ProxyFix).
forwarded_allow_ips = "*"
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
