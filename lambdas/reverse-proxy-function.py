import urllib3
import base64
import os

# Create HTTP client
http = urllib3.PoolManager()

# Get allowed ports from environment variables
DEFAULT_PORT = int(os.environ.get('DEFAULT_PORT', '3000'))
ALLOWED_PORTS = {DEFAULT_PORT}

# Parse allowed additional ports from environment
additional_ports_str = os.environ.get('ADDITIONAL_PORTS', '')
if additional_ports_str and additional_ports_str != 'NONE':
    for port_str in additional_ports_str.split(','):
        if port_str.strip().isdigit():
            ALLOWED_PORTS.add(int(port_str.strip()))

print(f"Reverse proxy initialized - Default port: {DEFAULT_PORT}, Allowed ports: {sorted(ALLOWED_PORTS)}")

def lambda_handler(event, context):
    """
    Lambda function to act as reverse proxy for tutorial sessions.
    Routes requests from sessionid.tutorial.domain to actual EC2 instance IPs.
    """

    try:
        # Extract host and path from ALB event
        headers = event.get('headers', {})
        host = headers.get('host', '')
        path = event.get('path', '/')
        query_string = event.get('queryStringParameters') or {}
        method = event.get('httpMethod', 'GET')
        body = event.get('body', '')
        is_base64 = event.get('isBase64Encoded', False)

        print(f"Request: {method} {host}{path}")

        # Parse session ID from subdomain: 1-2-3-4.tutorial.domain -> 1.2.3.4
        if '.' not in host:
            return error_response(400, "Invalid host header")

        session_id = host.split('.')[0]
        if not session_id or '-' not in session_id:
            return error_response(404, "Session not found")

        # Convert session ID back to IP: 1-2-3-4 -> 1.2.3.4
        try:
            target_ip = session_id.replace('-', '.')
            # Basic IP validation
            parts = target_ip.split('.')
            if len(parts) != 4 or not all(0 <= int(p) <= 255 for p in parts):
                raise ValueError("Invalid IP")
        except (ValueError, IndexError):
            return error_response(404, "Invalid session ID")

        # Determine target port from path
        target_port = DEFAULT_PORT
        original_path = path

        # Check for /portXXXX/ pattern
        if path.startswith('/port') and len(path) > 5:
            # Extract port number from path like /port8000/live
            try:
                # Find the end of the port number (next slash or end of string)
                port_start = 5  # After "/port"
                port_end = path.find('/', port_start)
                if port_end == -1:
                    port_end = len(path)

                port_str = path[port_start:port_end]
                if port_str.isdigit():
                    requested_port = int(port_str)

                    # Only allow configured ports
                    if requested_port in ALLOWED_PORTS:
                        target_port = requested_port
                        # Remove /portXXXX prefix: /port8000/live -> /live
                        original_path = path[port_end:] or '/'
                        print(f"Routing to allowed port {target_port}, path: {original_path}")
                    else:
                        print(f"Port {requested_port} not in allowed ports {sorted(ALLOWED_PORTS)}")
                        return error_response(403, f"Access to port {requested_port} not allowed")

            except (ValueError, IndexError):
                print(f"Invalid port in path: {path}, using default port {DEFAULT_PORT}")
                # Keep defaults: target_port = DEFAULT_PORT, original_path = path

        # Build target URL
        query_params = '&'.join([f"{k}={v}" for k, v in query_string.items()]) if query_string else ''
        target_url = f"http://{target_ip}:{target_port}{original_path}"
        if query_params:
            target_url += f"?{query_params}"

        print(f"Forwarding to: {target_url}")

        # Prepare headers for forwarding (remove ALB-specific headers)
        forward_headers = {}
        skip_headers = {'host', 'x-forwarded-for', 'x-forwarded-proto', 'x-forwarded-port', 'x-amzn-trace-id'}

        for key, value in headers.items():
            if key.lower() not in skip_headers:
                forward_headers[key] = value

        # Set correct host header for target
        forward_headers['Host'] = f"{target_ip}:{target_port}"

        # Decode body if base64 encoded
        if is_base64 and body:
            body = base64.b64decode(body).decode('utf-8')

        # Make request to target
        response = http.request(
            method=method,
            url=target_url,
            headers=forward_headers,
            body=body.encode('utf-8') if body else None,
            timeout=30,
            retries=False
        )

        # Prepare response headers
        response_headers = {}
        for key, value in response.headers.items():
            # Skip headers that ALB will set
            if key.lower() not in {'content-length', 'connection', 'transfer-encoding'}:
                response_headers[key] = value

        # Return ALB-compatible response
        return {
            'statusCode': response.status,
            'headers': response_headers,
            'body': response.data.decode('utf-8'),
            'isBase64Encoded': False
        }

    except urllib3.exceptions.HTTPError as e:
        print(f"HTTP error: {e}")
        return error_response(502, "Backend connection failed")
    except Exception as e:
        print(f"Unexpected error: {e}")
        return error_response(500, "Internal server error")

def error_response(status_code, message):
    """Return an error response"""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'text/html'
        },
        'body': f'''
        <html>
        <head><title>Tutorial Session Error</title></head>
        <body>
            <h1>Error {status_code}</h1>
            <p>{message}</p>
        </body>
        </html>
        ''',
        'isBase64Encoded': False
    }
