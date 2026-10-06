"""Gunicorn settings, resolved in Python rather than in the start command.

Railway supplies the port as $PORT. Reading it here instead of interpolating
it into a shell string means the start command needs no shell at all -- the
"'$PORT' is not a valid port number" failure happens when a command carrying
a literal $PORT is executed without one, which is easy to reintroduce via a
Custom Start Command in the service settings.
"""

import os

bind = "0.0.0.0:" + os.environ.get("PORT", "5000")

# Imports POST to NationBuilder one row at a time, so a few hundred rows can
# take a while. Keep the timeout generous.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))

accesslog = "-"
errorlog = "-"
