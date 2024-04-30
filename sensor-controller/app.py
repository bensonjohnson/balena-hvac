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
        'systemState': system_state,
        'Kp': pid.Kp,
        'Ki': pid.Ki,
        'Kd': pid.Kd,
    })

@app.route('/pid', methods=['POST'])
def update_pid():
    try:
        data = request.get_json()
        pid.Kp = float(data['Kp'])
        pid.Ki = float(data['Ki'])
        pid.Kd = float(data['Kd'])
        pid.setpoint = float(data['setpoint'])
        return jsonify({'message': 'PID parameters updated successfully'}), 200
    except KeyError as e:
        return jsonify({'error': f'Missing key in data: {str(e)}'}), 400
    except ValueError as e:
        return jsonify({'error': f'Invalid value for PID parameters: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500


# Sensor data storage
sensor_data = []

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

# Manual override flag
manual_override = False

@app.route('/toggle_system', methods=['POST'])
def toggle_system():
    global manual_override
    data = request.get_json()
    state = data.get('state', 'off').lower()

    if state == 'on':
        manual_override = False
        response_message = 'PID control reactivated'
    elif state == 'off':
        manual_override = True
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.LOW)
        response_message = 'System turned off, manual override activated'
    else:
        return jsonify({'error': 'Invalid state specified'}), 400
    
    return jsonify({'message': response_message}), 200

@app.route('/off', methods=['POST'])
def turn_off():
    global manual_override
    manual_override = True
    GPIO.output(coolingRelayPin, GPIO.LOW)
    GPIO.output(heatingRelayPin, GPIO.LOW)
    GPIO.output(fanRelayPin, GPIO.LOW)
    return jsonify({'message': 'All systems turned off'}), 200

@app.route('/manual_control', methods=['POST'])
def manual_control():
    global manual_override
    data = request.get_json()
    mode = data.get('mode', 'off').lower()
    manual_override = True
    if mode == 'off':
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.LOW)
        response_message = 'All systems turned off'
    elif mode == 'cooling':
        GPIO.output(coolingRelayPin, GPIO.HIGH)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.HIGH)
        response_message = 'Cooling mode activated'
    elif mode == 'heating':
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.HIGH)
        GPIO.output(fanRelayPin, GPIO.HIGH)
        response_message = 'Heating mode activated'
    else:
        manual_override = False
        return jsonify({'error': 'Invalid mode specified'}), 400
    return jsonify({'message': response_message}), 200

def adjust_relays(pid_output, current_temp):
    global manual_override
    if manual_override:
        return "Manual override active"
    if current_temp < setpointTempF - pid_output:
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.HIGH)
        GPIO.output(fanRelayPin, GPIO.HIGH)
        return "Heating"
    elif current_temp > setpointTempF + pid_output:
        GPIO.output(coolingRelayPin, GPIO.HIGH)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.HIGH)
        return "Cooling"
    else:
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.LOW)
        return "Off"
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
