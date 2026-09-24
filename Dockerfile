# Use official lightweight Python image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src ./src
COPY docs ./docs

# Expose the port Sanic runs on
EXPOSE 8000

# Start the application 
# (Ensure it binds to 0.0.0.0 so Azure can route traffic to it)
CMD ["python", "-m", "src.app", "--host=0.0.0.0", "--port=8000"]