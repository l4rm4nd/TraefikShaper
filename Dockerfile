FROM python:3.12.4-alpine

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY update_whitelist.py .
COPY dynamic-whitelist.yml .

CMD ["python", "update_whitelist.py"]
