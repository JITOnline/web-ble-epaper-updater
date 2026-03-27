FROM python:3.11-slim

# Install necessary system libraries for Bleak/Bluetooth and Pillow
RUN apt-get update && apt-get install -y \
    build-essential \
    libglib2.0-dev \
    libdbus-1-dev \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
