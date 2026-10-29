# FILE: gunicorn.conf.py
# Gunicorn loads this file automatically from the working directory, so these
# settings apply even though Render's start command sets workers/bind/timeout.

# One process (two workers ran out of memory on the 512 MB Starter plan), but
# several threads inside it. With the default sync worker, one slow request —
# a Twelve Data rate-limit wait or a slow phone download — froze the whole site.
worker_class = "gthread"
threads = 4


def post_worker_init(worker) -> None:
    """Start background jobs inside the worker, so they share its caches and rate limiter."""
    from main import start_background_tasks
    start_background_tasks()
