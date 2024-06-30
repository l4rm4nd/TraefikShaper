import os
import yaml
import time
import hmac
import hashlib
import requests
import secrets
import logging
from urllib.parse import urlencode
from flask import Flask, request, render_template_string, redirect
from random_word import RandomWords
from datetime import datetime, timedelta
import apprise

app = Flask(__name__)
app.logger.addHandler(logging.StreamHandler())  # Add stdout handler
app.logger.setLevel(logging.INFO)  # Set logging level to INFO

# Dictionary to store IP addresses along with their expiration times
ip_expiration = {}
# Dictionary to store IP addresses pending approval
pending_approval = {}
# Dictionary to store IP addresses along with their last request time
ip_last_request = {}

# Secret for generating secure approval tokens
SECRET_KEY = os.getenv('SECRET_KEY', secrets.token_hex(16))

# Get default expiration time from environment variable or default to 20 seconds
EXPIRATION_TIME = int(os.getenv('EXPIRATION_TIME', 300))

# Get hostname and protocol from environment variables
APPURL = os.getenv('APPURL', "http://localhost:5000")

# Get grant HTTP endpoint from environment variable or default to /knock-knock
GRANT_HTTP_ENDPOINT = os.getenv('GRANT_HTTP_ENDPOINT', '/knock-knock')

# Initialize Apprise object
apobj = apprise.Apprise()

# Get apprise notification string
APPRISE_NOTIFICATION_URL = os.getenv('APPRISE_NOTIFICATION_URL', 'unset')

if APPRISE_NOTIFICATION_URL != "unset":
    try:
        apobj.add(APPRISE_NOTIFICATION_URL)
    except Exception as e:
        app.logger.error(f"Failed to add Apprise notification url: {e}")

# Send notification using Apprise
def send_notification(message):
    try:
        if APPRISE_NOTIFICATION_URL != "unset":
            apobj.notify(body=message)
            return True
        else:
            return False
    except Exception as e:
        app.logger.error(f"Failed to send notification: {e}")
        return False

def overwrite_middleware():

    # Get default source range from environment variable
    DEFAULT_PRIVATE_CLASS_SOURCE_RANGE = os.getenv('DEFAULT_PRIVATE_CLASS_SOURCE_RANGE')
    # Get whitelisted IPs
    WHITELISTED_IPS = os.getenv('WHITELISTED_IPS', None)
    # Get IP strategy depth from environment variable or default to 0
    IPSTRATEGY_DEPTH = int(os.getenv('IPSTRATEGY_DEPTH', 0))
    # Get IP strategy exclude ips from environment variable
    EXCLUDED_IPS = os.getenv('EXCLUDED_IPS', None)

    if DEFAULT_PRIVATE_CLASS_SOURCE_RANGE == "True":
        # allow private class ranges as default
        DEFAULT_SOURCE_RANGE = ['127.0.0.1/32', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16']
    else:
        # allow localhost only as default
        DEFAULT_SOURCE_RANGE = ['127.0.0.1/32']

    if WHITELISTED_IPS != None:
        WHITELISTED_IPS = WHITELISTED_IPS.split(',')
        DEFAULT_SOURCE_RANGE.append(WHITELISTED_IPS)

    if EXCLUDED_IPS != None:
        EXCLUDED_IPS = EXCLUDED_IPS.split(',')
        
        # use ip strategy exclude ips, use the 
        whitelist = {
            'http': {
                'middlewares': {
                    'dynamic-ipwhitelist': {
                        'IPAllowList': {
                            'sourceRange': DEFAULT_SOURCE_RANGE,
                            'ipstrategy': {
                                'excludedips': EXCLUDED_IPS
                            }
                        }
                    }
                }
            }
        }
    else:
        # use ip strategy depth
        whitelist = {
            'http': {
                'middlewares': {
                    'dynamic-ipwhitelist': {
                        'IPAllowList': {
                            'sourceRange': DEFAULT_SOURCE_RANGE,
                            'ipstrategy': {
                                'depth': IPSTRATEGY_DEPTH
                            }
                        }
                    }
                }
            }
        }

    # Overwrite the middleware file to ensure only 127.0.0.1/32 is added
    whitelist_file = 'dynamic-whitelist.yml'

    with open(whitelist_file, 'w') as file:
        yaml.dump(whitelist, file)

@app.route(GRANT_HTTP_ENDPOINT, methods=['GET'])
def update_whitelist():

    whitelist_file = 'dynamic-whitelist.yml'

    # Load existing whitelist or initialize as empty dictionary
    with open(whitelist_file, 'r') as file:
        whitelist = yaml.safe_load(file) or {}

    try:
        ip = request.headers.get('X-Forwarded-For').split(",")[0]
    except:
        ip = request.headers.get('X-Forwarded-For')

    if ip == None:
        ip = request.remote_addr

    # Check if there is a pending approval for the IP within the last hour
    if f'{ip}/32' not in whitelist['http']['middlewares']['dynamic-ipwhitelist']['IPAllowList']['sourceRange']:
        if ip in ip_last_request:
            last_request_time = ip_last_request[ip]
            if datetime.now() - last_request_time < timedelta(minutes=5):
                return 'You have already requested approval within the last 5 minutes.', 403
    else:
        # ip already whitelisted; redirect user
        return redirect('/')

    # Update the last request time for the IP
    ip_last_request[ip] = datetime.now()

    # Get expiration time
    expiration_time = EXPIRATION_TIME

    expiration = time.time() + expiration_time  # Set expiration time

    # Update expiration time for the IP address
    ip_expiration[ip] = expiration

    # Ensure 127.0.0.1/32 is always present
    if '127.0.0.1/32' not in whitelist['http']['middlewares']['dynamic-ipwhitelist']['IPAllowList']['sourceRange']:
        whitelist['http']['middlewares']['dynamic-ipwhitelist']['IPAllowList']['sourceRange'] = ['127.0.0.1/32']

    # Check if IP address already exists in sourceRange
    if f'{ip}/32' not in whitelist['http']['middlewares']['dynamic-ipwhitelist']['IPAllowList']['sourceRange']:
        # Generate a token for the IP address
        token = generate_token(ip)

        # Store IP address and token for pending approval
        pending_approval[ip] = {'expiration_time': expiration_time, 'token': token}

        # Construct approval link with FQDN URL, IP address, and token
        approval_link = f'{APPURL}/approve?ip={ip}&token={token}&expiration_time={expiration_time}'

        # URL encode the text message
        random_word = RandomWords().get_random_word()
        message = f"{approval_link}&validation_code={random_word}"

        notification_result = send_notification(message)

        # Check if the request was successful (status code 200)
        if notification_result == True:
            # Construct the HTML response with CSS styling
            html_response = """
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Approval Required</title>
                <style>
                    body {
                        font-family: Arial, sans-serif;
                        background-color: #f4f4f4;
                        padding: 20px;
                    }
                    .container {
                        max-width: 600px;
                        margin: auto;
                        background-color: #fff;
                        border-radius: 5px;
                        padding: 20px;
                        box-shadow: 0 2px 5px rgba(0, 0, 0, 0.1);
                    }
                    h1 {
                        color: #333;
                    }
                    p {
                        color: #555;
                        margin-bottom: 20px;
                    }
                    .highlight {
                        background-color: #ffffcc;
                        padding: 5px;
                        font-weight: bold;
                    }
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>Approval Required</h1>
                    <p>Your request requires approval. Please wait while we process your request.</p>
                    <p>Validation code: <span class="highlight">{{ random_word }}</span></p>
                    <p>An administrator will review your request shortly.</p>
                </div>
            </body>
            </html>
            """
            return render_template_string(html_response, random_word=random_word), 200
        else:
            app.logger.info(f"Default notification channel failed. Here is the approval link via container logs: {approval_link}")
            return f'Approval required, but message to admin failed', 403
    else:
        return redirect('/')

@app.route('/approve', methods=['GET'])
def approve_ip():
    ip = request.args.get('ip')
    token = request.args.get('token')
    expiration_time = int(request.args.get('expiration_time', EXPIRATION_TIME))

    # Check if IP and token are valid
    if ip in pending_approval and pending_approval[ip]['token'] == token:
        whitelist_file = 'dynamic-whitelist.yml'

        # Load existing whitelist or initialize as empty dictionary
        with open(whitelist_file, 'r') as file:
            whitelist = yaml.safe_load(file) or {}

        # Ensure 127.0.0.1/32 is always present
        if '127.0.0.1/32' not in whitelist['http']['middlewares']['dynamic-ipwhitelist']['IPAllowList']['sourceRange']:
            whitelist['http']['middlewares']['dynamic-ipwhitelist']['IPAllowList']['sourceRange'] = ['127.0.0.1/32']

        # Append IP address to sourceRange
        whitelist['http']['middlewares']['dynamic-ipwhitelist']['IPAllowList']['sourceRange'].append(f'{ip}/32')

        # Write updated whitelist to file
        with open(whitelist_file, 'w') as file:
            yaml.dump(whitelist, file)

        # Remove IP from pending approval list
        del pending_approval[ip]

        expiration = time.time() + expiration_time
        ip_expiration[ip] = expiration

        message = f"✅ Whitelisted {ip} for {expiration_time} seconds."

        notification_result = send_notification(message)

        if notification_result:
            return 'IP address approved and added to whitelist.', 200
        else:
            return 'IP address approved but notification failed.', 200

    return 'Invalid token or IP address.', 403

# Generate a token for the IP address
def generate_token(ip):
    secret_key = SECRET_KEY
    current_time = str(int(time.time()))  # Get current time as string
    data = ip + current_time  # Concatenate IP and current time
    signature = hmac.new(secret_key.encode(), data.encode(), hashlib.sha256).hexdigest()
    return signature

# Periodically check and remove expired IP addresses from the whitelist
def remove_expired_ips():
    while True:
        current_time = time.time()
        expired_ips = [ip for ip, expiration_time in ip_expiration.items() if expiration_time < current_time]

        if expired_ips:
            whitelist_file = 'dynamic-whitelist.yml'

            # Load existing whitelist or initialize as empty dictionary
            with open(whitelist_file, 'r') as file:
                whitelist = yaml.safe_load(file) or {}

            # Ensure 127.0.0.1/32 is always present
            if '127.0.0.1/32' not in whitelist['http']['middlewares']['dynamic-ipwhitelist']['IPAllowList']['sourceRange']:
                whitelist['http']['middlewares']['dynamic-ipwhitelist']['IPAllowList']['sourceRange'] = ['127.0.0.1/32']

            # Remove expired IPs from whitelist and send notification
            for ip in expired_ips:
                if f'{ip}/32' in whitelist['http']['middlewares']['dynamic-ipwhitelist']['IPAllowList']['sourceRange']:
                    source_range = whitelist['http']['middlewares']['dynamic-ipwhitelist']['IPAllowList']['sourceRange']
                    source_range.remove(f'{ip}/32')

                    # Write updated whitelist to file
                    with open(whitelist_file, 'w') as file:
                        yaml.dump(whitelist, file)

                    message = f"❌ Removed {ip} from middleware. Access revoked."

                    notification_result = send_notification(message)

                    # Remove expired IP from ip_expiration dictionary
                    del ip_expiration[ip]

        time.sleep(5)  # Check every 5 seconds for expired IPs

if __name__ == '__main__':
    # Start a separate thread to periodically remove expired IPs
    import threading
    thread = threading.Thread(target=remove_expired_ips)
    thread.start()

    # Call the function to overwrite middleware at startup
    overwrite_middleware()
    message = "⭐ TraefikShaper Enabled | IPAllowList resetted ⭐"
    send_notification(message)

    # Start Flask app
    app.run(host='0.0.0.0', port=5000)
