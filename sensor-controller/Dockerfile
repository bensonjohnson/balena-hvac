FROM balenalib/raspberrypi3-64-alpine-python:3.15-build as build
WORKDIR /usr/src/app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "app.py"]
EXPOSE 5000
