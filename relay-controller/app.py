import threading
import time
import datetime
from flask import Flask, jsonify, request
import redis
import json
import RPi.GPIO as GPIO
import sys
import os
import signal
from simple_pid import PID

app = Flask(__name__)

# Initialize Redis client
redis_client = redis.StrictRedis(host='localhost', port=6379, db=0)

# GPIO setup
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# Define GPIO pins for relays
heatingRelayPin = 17
coolingRelayPin = 27
fanRelayPin = 22

GPIO.setup(heatingRelayPin, GPIO.OUT)
GPIO.setup(coolingRelayPin, GPIO.OUT)
GPIO.setup(fanRelayPin, GPIO.OUT)

# Ensure all relays are off at startup
GPIO.output(heatingRelayPin, GPIO.LOW)
GPIO.output(coolingRelayPin, GPIO.LOW)
GPIO.output(fanRelayPin, GPIO.LOW)

# PID controller setup
pid = PID(Kp=0.5, Ki=0.1, Kd=0.01, setpoint=70)
pid.output_limits = (0, 10)  # Adjust output limits as needed

# Global variables
pid_enabled = False
setpointTempF = 70  # Default setpoint temperature
selected_mode = 'average'
selected_sensor_name = None

# Initialize pid_enabled from Redis
pid_enabled_redis = redis_client.get('pid_enabled')
if pid_enabled_redis is not None:
    pid_enabled = pid_enabled_redis.decode('utf-8') == 'True'
else:
    redis_client.set('pid_enabled', 'False')

# Ensure default heating and cooling settings are set in Redis
if redis_client.get('heating_enabled') is None:
    redis_client.set('heating_enabled', 'True')

if redis_client.get('cooling_enabled') is None:
    redis_client.set('cooling_enabled', 'True')

# Control loop function
def control_loop():
    while True:
        try:
            # Fetch temperature data from Redis
            sensor_data_json = redis_client.get('sensor_data')
            if sensor_data_json:
                sensor_data = json.loads(sensor_data_json)
            else:
                sensor_data = {}

            temperatures = []

            if selected_mode == 'average':
                # Calculate average temperature
                for sensor_name, readings in sensor_data.items():
                    if readings:
                        temperatures.append(readings[-1]['temperature'])
                if temperatures:
                    average_temperature = sum(temperatures) / len(temperatures)
                else:
                    average_temperature = None
            else:
                # Use specific sensor
                if selected_sensor_name in sensor_data and sensor_data[selected_sensor_name]:
                    average_temperature = sensor_data[selected_sensor_name][-1]['temperature']
                else:
                    average_temperature = None

            if average_temperature is not None:
                pid.setpoint = setpointTempF
                pid_value = pid(average_temperature)
                system_state = adjust_relays(pid_value, average_temperature)
                redis_client.set('average_temperature', average_temperature)
                redis_client.set('pid_value', pid_value)
                redis_client.set('system_state', system_state)
            else:
                # No valid temperature data; turn off all relays
                GPIO.output(coolingRelayPin, GPIO.LOW)
                GPIO.output(heatingRelayPin, GPIO.LOW)
                GPIO.output(fanRelayPin, GPIO.LOW)
                redis_client.set('system_state', 'No Data')

            time.sleep(5)  # Adjust the sleep time as needed
        except Exception as e:
            print(f"Error in control loop: {e}")
            time.sleep(5)

# Start the control loop in a separate thread
control_thread = threading.Thread(target=control_loop)
control_thread.daemon = True
control_thread.start()

# Function to adjust relays based on PID output
def adjust_relays(pid_output, average_temperature):
    global pid_enabled

    if not pid_enabled:
        # PID control is disabled; do not adjust relays
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.LOW)
        redis_client.set('system_state', 'System Off')
        return "System Off"

    # Read heating and cooling settings from Redis
    heating_enabled = redis_client.get('heating_enabled')
    cooling_enabled = redis_client.get('cooling_enabled')

    heating_enabled = heating_enabled.decode('utf-8') == 'True' if heating_enabled else True
    cooling_enabled = cooling_enabled.decode('utf-8') == 'True' if cooling_enabled else True

    # Automatic control (PID active)
    if average_temperature < setpointTempF - pid_output:
        if heating_enabled:
            GPIO.output(coolingRelayPin, GPIO.LOW)
            GPIO.output(heatingRelayPin, GPIO.HIGH)
            GPIO.output(fanRelayPin, GPIO.HIGH)
            redis_client.set('system_state', 'Heating')
            return "Heating"
        else:
            # Heating is disabled; system remains idle
            GPIO.output(coolingRelayPin, GPIO.LOW)
            GPIO.output(heatingRelayPin, GPIO.LOW)
            GPIO.output(fanRelayPin, GPIO.LOW)
            redis_client.set('system_state', 'Idle (Heating Disabled)')
            return "Idle (Heating Disabled)"
    elif average_temperature > setpointTempF + pid_output:
        if cooling_enabled:
            GPIO.output(coolingRelayPin, GPIO.HIGH)
            GPIO.output(heatingRelayPin, GPIO.LOW)
            GPIO.output(fanRelayPin, GPIO.HIGH)
            redis_client.set('system_state', 'Cooling')
            return "Cooling"
        else:
            # Cooling is disabled; system remains idle
            GPIO.output(coolingRelayPin, GPIO.LOW)
            GPIO.output(heatingRelayPin, GPIO.LOW)
            GPIO.output(fanRelayPin, GPIO.LOW)
            redis_client.set('system_state', 'Idle (Cooling Disabled)')
            return "Idle (Cooling Disabled)"
    else:
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.LOW)
        redis_client.set('system_state', 'Idle')
        return "Idle"

# API endpoint to get system status
@app.route('/api/getstatus', methods=['GET'])
def get_status():
    try:
        # Fetch data from Redis
        average_temperature = redis_client.get('average_temperature')
        pid_value = redis_client.get('pid_value')
        system_state = redis_client.get('system_state')
        set_temp = redis_client.get('setpointTempF')

        # Read heating and cooling settings from Redis
        heating_enabled = redis_client.get('heating_enabled')
        cooling_enabled = redis_client.get('cooling_enabled')

        heating_enabled = heating_enabled.decode('utf-8') == 'True' if heating_enabled else True
        cooling_enabled = cooling_enabled.decode('utf-8') == 'True' if cooling_enabled else True

        # Convert bytes to appropriate types
        average_temperature = float(average_temperature) if average_temperature else None
        pid_value = float(pid_value) if pid_value else None
        system_state = system_state.decode('utf-8') if system_state else 'Unknown'
        set_temp = float(set_temp) if set_temp else setpointTempF

        # Fetch sensor data
        sensor_data_json = redis_client.get('sensor_data')
        if sensor_data_json:
            sensor_data = json.loads(sensor_data_json)
        else:
            sensor_data = {}

        return jsonify({
            'average_temperature': average_temperature,
            'pidValue': pid_value,
            'systemState': system_state,
            'setTemperature': set_temp,
            'pidEnabled': pid_enabled,
            'heating_enabled': heating_enabled,
            'cooling_enabled': cooling_enabled,
            'Kp': pid.Kp,
            'Ki': pid.Ki,
            'Kd': pid.Kd,
            'sensorData': sensor_data,
            'selectedMode': selected_mode,
            'selectedSensor': selected_sensor_name
        })
    except Exception as e:
        print(f"Error in /api/getstatus: {e}")
        return jsonify({'error': str(e)}), 500

# API endpoint to set new temperature
@app.route('/api/settemp', methods=['POST'])
def set_temperature():
    global setpointTempF
    new_temp = request.form.get('settemp')
    if new_temp:
        try:
            setpointTempF = float(new_temp)
            pid.setpoint = setpointTempF
            redis_client.set('setpointTempF', setpointTempF)
            return jsonify({'message': 'Set temperature updated successfully'}), 200
        except ValueError:
            return jsonify({'error': 'Invalid temperature value'}), 400
    else:
        return jsonify({'error': 'Temperature value is required'}), 400

# API endpoint to set mode (average or specific sensor)
@app.route('/api/set_mode', methods=['POST'])
def set_mode():
    global selected_mode, selected_sensor_name
    data = request.get_json()
    mode = data.get('mode')
    sensor_name = data.get('sensor_name')

    if mode not in ['average', 'specific']:
        return jsonify({'error': 'Invalid mode'}), 400

    selected_mode = mode
    selected_sensor_name = sensor_name if mode == 'specific' else None

    # Save to Redis for persistence if needed
    redis_client.set('selected_mode', selected_mode)
    redis_client.set('selected_sensor_name', selected_sensor_name or '')

    return jsonify({'message': f'Mode set to {selected_mode}'}), 200

# API endpoint to toggle the system (enable/disable PID control)
@app.route('/api/toggle_system', methods=['POST'])
def toggle_system():
    global pid_enabled
    data = request.get_json()
    state = data.get('state')

    if state == 'on':
        pid_enabled = True
    elif state == 'off':
        pid_enabled = False
        # Turn off all relays when PID is disabled
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.LOW)
        redis_client.set('system_state', 'System Off')
    else:
        return jsonify({'error': 'Invalid state'}), 400

    # Save pid_enabled state to Redis
    redis_client.set('pid_enabled', str(pid_enabled))

    return jsonify({'message': f'System turned {state}'}), 200

# API endpoint to get seasonal settings
@app.route('/api/get_seasonal_settings', methods=['GET'])
def get_seasonal_settings():
    heating_enabled = redis_client.get('heating_enabled')
    cooling_enabled = redis_client.get('cooling_enabled')

    heating_enabled = heating_enabled.decode('utf-8') == 'True' if heating_enabled else True
    cooling_enabled = cooling_enabled.decode('utf-8') == 'True' if cooling_enabled else True

    return jsonify({
        'heating_enabled': heating_enabled,
        'cooling_enabled': cooling_enabled
    })

# API endpoint to set seasonal settings
@app.route('/api/set_seasonal_settings', methods=['POST'])
def set_seasonal_settings():
    data = request.get_json()
    heating = data.get('heating_enabled')
    cooling = data.get('cooling_enabled')

    if heating is not None:
        heating_enabled = bool(heating)
        redis_client.set('heating_enabled', str(heating_enabled))
    if cooling is not None:
        cooling_enabled = bool(cooling)
        redis_client.set('cooling_enabled', str(cooling_enabled))

    return jsonify({'message': 'Seasonal settings updated successfully'}), 200

# Clean up GPIO pins on exit
def cleanup_gpio(signum, frame):
    GPIO.cleanup()
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup_gpio)
signal.signal(signal.SIGTERM, cleanup_gpio)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
