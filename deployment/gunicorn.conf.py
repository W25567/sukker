"""Gunicorn på serveren. Bindingen (unix-socket under /run/<service>) angives i systemd-servicen."""
import multiprocessing
import os

workers = int(os.environ.get("SUKKER_GUNICORN_WORKERS") or min(4, multiprocessing.cpu_count() * 2 + 1))
timeout = 30
graceful_timeout = 20
max_requests = 1000  # nye arbejdsprocesser med jævne mellemrum begrænser hukommelseslæk
max_requests_jitter = 100
umask = 0o007  # Nginx (i gruppen for servicebrugeren) skal kunne forbinde til socketten
accesslog = None  # Nginx logger forespørgsler uden personoplysninger (specifikation 13.2)
errorlog = "-"  # til journalen
loglevel = "info"
proc_name = os.environ.get("SUKKER_SERVICE_NAME", "sukker")
# Gunicorn 25.1+ opretter ellers en kontrolsocket i hjemmemappen, som er skrivebeskyttet under systemd-hærdningen.
control_socket_disable = True
