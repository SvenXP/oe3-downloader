FROM selenium/standalone-chrome:latest

USER root

# Install Python, pip, ffmpeg (needed by yt-dlp for audio conversion)
RUN apt-get update && apt-get install -y \
    python3 python3-pip ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --break-system-packages -r requirements.txt

COPY app/ .

CMD ["python3", "main.py"]