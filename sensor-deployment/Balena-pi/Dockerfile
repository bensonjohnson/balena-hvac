FROM balenalib/raspberrypi0-2w-64-alpine-python:build
WORKDIR /usr/src/app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "app.py"]