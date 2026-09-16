FROM python:3.12-slim

# System dependencies (minimal — pyhockey needs nothing fancy)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Set timezone for proper "today" calculations
ENV TZ=America/Los_Angeles

WORKDIR /app

# Install Python deps first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY *.py ./
COPY scouts/ ./scouts/

# Auth tokens, logs, and scout state snapshots are mounted as volumes
# so they persist across container restarts
VOLUME ["/app/auth", "/app/logs", "/app/state"]

# Default to running the morning briefing — overridden by docker run / cron
CMD ["python", "main.py", "--email", "--mode", "morning"]
