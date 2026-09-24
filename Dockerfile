# Use official lightweight Python image
FROM python:3.12-slim

# Send logs straight to stdout (visible in Azure's log stream) and skip
# writing .pyc files into the image
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first so this layer is cached between code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code only -- docs/ isn't needed at runtime
COPY src ./src

# Run as an unprivileged user
RUN useradd --create-home --uid 1000 appuser
USER appuser

# Sanic listens on PORT (default 8000) and binds to 0.0.0.0 in src/app.py.
# Azure's ingress target port is 8000 to match.
EXPOSE 8000

CMD ["python", "-m", "src.app"]