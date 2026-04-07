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

# Auth tokens and logs are mounted as volumes (see compose file)
# so they persist across container restarts
VOLUME ["/app/auth", "/app/logs"]

# Default to running the morning briefing — overridden by docker run / cron
CMD ["python", "main.py", "--email", "--mode", "morning"]
