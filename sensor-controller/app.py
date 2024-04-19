from flask import Flask, request, jsonify
import RPi.GPIO as GPIO
from simple_pid import PID
import time
import board
import busio
import adafruit_sht31d
from flask_cors import CORS


app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Sensor data storage
sensor_data = []

# GPIO Pins (Use BCM numbering)
coolingRelayPin = 18 #purple
heatingRelayPin = 23 #blue
fanRelayPin = 24 #green

# Setup GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setup(coolingRelayPin, GPIO.OUT)
GPIO.setup(heatingRelayPin, GPIO.OUT)
GPIO.setup(fanRelayPin, GPIO.OUT)
time.sleep(0.1)  # Wait for 100ms to ensure GPIO states are set
GPIO.output(coolingRelayPin, GPIO.LOW)
GPIO.output(heatingRelayPin, GPIO.LOW)
GPIO.output(fanRelayPin, GPIO.LOW)

# Initialize the I2C bus
i2c = busio.I2C(board.SCL, board.SDA)
# Initialize the SHT31D sensor
sensor = adafruit_sht31d.SHT31D(i2c)

# PID setup
setpointTempF = 68.0  # Default setpoint
pid = PID(0.5, 0.1, 0.01, setpoint=setpointTempF)
pid.output_limits = (0, 1)  # Output will be between 0 and 1

@app.route('/submit_sensor_data', methods=['POST'])
def submit_sensor_data():
    data = request.get_json()
    sensor_data.append(data)
    return jsonify({'message': 'Sensor data received'})

@app.route('/process_sensors', methods=['GET'])
def process_sensors():
    global setpointTempF
    farthest_sensor = get_sensor_farthest_from_setpoint()
    if farthest_sensor:
        temperature_f = farthest_sensor['temperature']
        pid_value = pid(temperature_f)
        system_state = adjust_relays(pid_value, temperature_f)
        return jsonify({
            'temperature': temperature_f,
            'humidity': farthest_sensor.get('humidity', None),
            'setTemperature': setpointTempF,
            'pidValue': pid_value,
            'systemState': system_state
        })
    return jsonify({'message': 'No sensor data available'})

def get_sensor_farthest_from_setpoint():
    if not sensor_data:
        return None
    # Calculate the sensor that is farthest from the setpoint
    return max(sensor_data, key=lambda x: abs(x['temperature'] - setpointTempF))

@app.route('/getstatus', methods=['GET'])
def get_status():
    temperature_c = sensor.temperature
    temperature_f = temperature_c * 9.0 / 5.0 + 32.0
    temperature_f = round(temperature_f, 2)
    humidity = sensor.relative_humidity
    pid_value = pid(temperature_f)
    system_state = adjust_relays(pid_value, temperature_f)  # Get the system state
    return jsonify({
        'temperature': temperature_f,
        'humidity': humidity,
        'setTemperature': setpointTempF,
        'pidValue': pid_value,
        'systemState': system_state
    })

@app.route('/settemp', methods=['POST'])
def set_temp():
    global setpointTempF
    new_temp = float(request.form['settemp'])
    setpointTempF = new_temp
    pid.setpoint = new_temp
    return jsonify({'message': 'Set temperature updated to {} °F'.format(new_temp)})

def adjust_relays(pid_output, current_temp):
    if current_temp < setpointTempF - pid_output:
        # Turn on heating
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.HIGH)
        GPIO.output(fanRelayPin, GPIO.HIGH)
        return "Heating"
    elif current_temp > setpointTempF + pid_output:
        # Turn on cooling
        GPIO.output(coolingRelayPin, GPIO.HIGH)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.HIGH)
        return "Cooling"
    else:
        # Turn off all
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.LOW)
        return "Off"
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
