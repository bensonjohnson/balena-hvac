import os
import board
import busio
import adafruit_sht31d
import time
import requests

# Initialize the I2C bus
i2c = busio.I2C(board.SCL, board.SDA)

# Initialize the SHT31D sensor
sensor = adafruit_sht31d.SHT31D(i2c)

def read_sensor_data():
    while True:
        # Read temperature and humidity from the sensor
        temperature_c = sensor.temperature
        humidity = sensor.relative_humidity

        # Convert Celsius to Fahrenheit and round to the nearest thousandth
        temperature_f = round(temperature_c * (9.0 / 5.0) + 32, 3)
        humidity = round(humidity, 3)

        # Retrieve sensor name from environment variable or use a default
        sensor_name = os.getenv('SENSOR_NAME', 'default_sensor')

        # Prepare data payload
        data = {
            'sensorname': sensor_name,
            'Temperature': temperature_f,
            'Humidity': humidity
        }

        # URL of the server where data will be sent
        url = os.getenv('URL', 'http://your-server-url.com/submit_sensor_data')

        try:
            response = requests.post(url, json=data)
            print("Data submitted successfully. Server response:", response.text)
        except requests.exceptions.RequestException as e:
            print("Failed to submit data:", e)

       
        time.sleep(15)

if __name__ == '__main__':
    read_sensor_data()
