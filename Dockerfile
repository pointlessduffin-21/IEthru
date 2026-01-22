# Use official Playwright image which includes Python, browsers, and dependencies
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Environment variables
ENV APP_HOST=0.0.0.0
ENV APP_PORT=5000
ENV APP_ENV=prod
# Required for Playwright in Docker
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 5000

# Health check (curl is standard in this image)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/api/status || exit 1

# Run the application
CMD ["python", "app.py"]
