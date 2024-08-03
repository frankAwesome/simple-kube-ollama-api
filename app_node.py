import pika
import ast
import os
import requests
import json
import logging
from kafka import KafkaProducer
from prometheus_client import start_http_server, Summary, Counter, Gauge
from logging_loki import LokiHandler

# Loki Configuration
loki_host = os.getenv('LOKI_HOST', 'http://localhost:3100')
loki_labels = {"job": "python-app"}  # Add any other labels you want

# Set up Loki logging
loki_handler = LokiHandler(
    url=f"{loki_host}/loki/api/v1/push",
    tags=loki_labels,
    version="1"
)
logging.basicConfig(level=logging.INFO, handlers=[loki_handler], format='%(asctime)s - %(levelname)s - %(message)s')

# Prometheus metrics
REQUEST_TIME = Summary('request_processing_seconds', 'Time spent processing request')
REQUEST_COUNTER = Counter('request_count', 'Total number of requests')
ERROR_COUNTER = Counter('error_count', 'Total number of errors')
REQUEST_IN_PROGRESS = Gauge('requests_in_progress', 'Number of requests in progress')

# Start Prometheus HTTP server
start_http_server(8000)

def log_info(message):
    logging.info(message)

def log_error(message):
    logging.error(message)

@REQUEST_TIME.time()
@REQUEST_IN_PROGRESS.track_inprogress()
def on_request(ch, method, props, body):
    REQUEST_COUNTER.inc()
    log_info("Received RPC request.")

    try:
        string_representation = body.decode('utf-8')  # Decode byte string to a normal string
        dictionary = ast.literal_eval(string_representation)
        log_info("Decoded request body and converted to dictionary.")
    except Exception as e:
        log_error(f"Failed to decode and convert request body: {e}")
        ERROR_COUNTER.inc()
        return

    input_text = str(dictionary.get('prompt', ''))
    log_info(f"Extracted prompt: {input_text}")

    # URL and data for the request
    ollama_ip = os.getenv('OLLAMAIP', 'localhost')
    url = f'http://{ollama_ip}:11434/api/generate'
    data = {'model': 'llama3', 'prompt': input_text}
    log_info(f"Sending POST request to {url} with data: {data}")

    # Send the POST request
    try:
        response = requests.post(url, data=json.dumps(data), headers={'Content-Type': 'application/json'})
        response.raise_for_status()
        log_info("Received response from external API.")
    except requests.RequestException as e:
        log_error(f"Error during external API request: {e}")
        ERROR_COUNTER.inc()
        ch.basic_publish(
            exchange='',
            routing_key=props.reply_to,
            properties=pika.BasicProperties(correlation_id=props.correlation_id),
            body=str(e)
        )
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return

    responses = response.content.decode('utf-8')
    response_objects = responses.split('\n')

    # Convert each JSON object into a dictionary and store them in a list
    response_list = []
    for obj in response_objects:
        if obj.strip():  # Ignore empty lines
            try:
                response_list.append(json.loads(obj))
            except json.JSONDecodeError as e:
                log_error(f"Failed to decode JSON object: {e}")
                ERROR_COUNTER.inc()

    # Extract and consolidate the responses into one sentence
    consolidated_response = ''.join([item.get('response', '') for item in response_list])
    response = consolidated_response
    log_info(f"Consolidated response: {response}")

    # Kafka
    kafka_host = f"{os.getenv('KAFKAIP', 'localhost')}:9092"
    try:
        producer = KafkaProducer(bootstrap_servers=kafka_host,
                                 value_serializer=lambda v: json.dumps(v).encode('utf-8'))
        message = {'prompt': input_text, 'response': response}
        producer.send('prompt-events', message)
        producer.flush()
        log_info(f"Sent message to Kafka: {message}")
    except Exception as e:
        log_error(f"Failed to send message to Kafka: {e}")
        ERROR_COUNTER.inc()

    # Send response back to RPC client
    ch.basic_publish(
        exchange='',
        routing_key=props.reply_to,
        properties=pika.BasicProperties(correlation_id=props.correlation_id),
        body=str(response)
    )
    ch.basic_ack(delivery_tag=method.delivery_tag)
    log_info("Sent response back to RPC client and acknowledged the message.")

# Set up RabbitMQ connection and channel
rabbitmq_ip = os.getenv('RABBITIP', 'localhost')
connection = pika.BlockingConnection(pika.ConnectionParameters(rabbitmq_ip))
channel = connection.channel()

channel.queue_declare(queue='rpc_queue')
channel.basic_qos(prefetch_count=1)
channel.basic_consume(queue='rpc_queue', on_message_callback=on_request)

log_info("Awaiting RPC requests.")
channel.start_consuming()
