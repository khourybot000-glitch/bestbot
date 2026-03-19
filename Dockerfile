FROM python:3.9-slim

RUN apt-get update && apt-get install -y \
    wget gnupg unzip curl google-chrome-stable \
    --no-install-recommends && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
