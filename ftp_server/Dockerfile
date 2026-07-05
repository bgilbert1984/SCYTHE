FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY ftp_server.py ./

EXPOSE 2121 60000-60010

ENTRYPOINT ["python", "ftp_server.py"]
