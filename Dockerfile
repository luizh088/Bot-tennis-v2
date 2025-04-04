FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

RUN playwright install chromium

COPY . .

# Execute explicitamente com saída contínua no terminal
CMD ["python", "-u", "bot.py"]
