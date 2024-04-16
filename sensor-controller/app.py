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

# GPIO Pins (Use BCM numbering)
coolingRelayPin = 18
heatingRelayPin = 23
fanRelayPin = 24

# Setup GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setup(coolingRelayPin, GPIO.OUT)
GPIO.setup(heatingRelayPin, GPIO.OUT)
GPIO.setup(fanRelayPin, GPIO.OUT)

# Initialize all relays to off
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
