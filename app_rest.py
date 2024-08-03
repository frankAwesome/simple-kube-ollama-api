import pika
import uuid
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import os
import psutil
from prometheus_client import start_http_server, Summary, Gauge, Counter, generate_latest
import logging
import redis
import json
from logging_loki import LokiHandler

app = Flask(__name__)
CORS(app)  # This will enable CORS for all routes

# Prometheus metrics
REQUEST_TIME = Summary('request_processing_seconds', 'Time spent processing request')
CPU_USAGE = Gauge('cpu_usage', 'CPU usage')
MEMORY_USAGE = Gauge('memory_usage', 'Memory usage')
REQUEST_COUNTER = Counter('http_requests_total', 'Total number of HTTP requests')

# Loki Configuration
loki_host = os.getenv('LOKI_HOST', 'http://localhost:3100')
loki_labels = {"job": "flask_app"}  # Add any other labels you want

# Set up Loki logging
loki_handler = LokiHandler(
    url=f"{loki_host}/loki/api/v1/push",
    tags=loki_labels,
    version="1"
)
logging.basicConfig(level=logging.INFO, handlers=[loki_handler], format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize Redis client
redis_host = os.getenv('REDIS_HOST', 'localhost')
redis_port = 6379
redis_client = redis.StrictRedis(host=redis_host, port=redis_port, decode_responses=True)


class RabbitMQClient:
    def __init__(self):
        rabbitmq_ip = os.getenv('RABBITIP', 'localhost')
        self.connection = pika.BlockingConnection(pika.ConnectionParameters(rabbitmq_ip))
        self.channel = self.connection.channel()
        self.channel.queue_declare(queue='rpc_queue')
        self.callback_queue = self.channel.queue_declare(queue='', exclusive=True).method.queue
        self.channel.basic_consume(
            queue=self.callback_queue,
            on_message_callback=self.on_response,
            auto_ack=True
        )
        self.response = None
        self.corr_id = None
        logger.info('RabbitMQClient initialized.')

    def on_response(self, ch, method, props, body):
        if self.corr_id == props.correlation_id:
            self.response = body
        logger.debug(f'Received response: {body}')

    @REQUEST_TIME.time()  # Measure the time taken by the call method
    def call(self, n):
        logger.info(f'Sending request to RabbitMQ with data: {n}')
        self.response = None
        self.corr_id = str(uuid.uuid4())
        self.channel.basic_publish(
            exchange='',
            routing_key='rpc_queue',
            properties=pika.BasicProperties(
                reply_to=self.callback_queue,
                correlation_id=self.corr_id
            ),
            body=str(n)
        )
        while self.response is None:
            self.connection.process_data_events()
        logger.info(f'Received response from RabbitMQ: {self.response.decode()}')
        return self.response.decode()


@app.route('/prompt_llm', methods=['POST'])
def prompt_llm():
    REQUEST_COUNTER.inc()  # Increment the request counter
    data = request.get_json()
    logger.info(f'Received /prompt_llm request with data: {data}')

    # Check if response is in Redis cache
    cache_key = f"prompt_llm:{json.dumps(data)}"
    cached_response = redis_client.get(cache_key)

    if cached_response:
        logger.info('Found response in cache.')
        return jsonify({"response": cached_response})

    # If not in cache, call RabbitMQ
    rabbitmq = RabbitMQClient()
    response = rabbitmq.call(data)

    # Cache the response
    redis_client.set(cache_key, response, ex=3600)  # Cache for 1 hour
    logger.info('Cached new response.')

    logger.info(f'Sending /prompt_llm response: {response}')
    return jsonify({"response": response})


@app.route('/metrics', methods=['GET'])
def metrics():
    cpu_usage = psutil.cpu_percent()
    memory_usage = psutil.virtual_memory().percent
    CPU_USAGE.set(cpu_usage)
    MEMORY_USAGE.set(memory_usage)
    metrics_data = {
        'cpu_usage': cpu_usage,
        'memory_usage': memory_usage
    }
    logger.info(f'Returning /metrics data: {metrics_data}')
    return Response(generate_latest(), mimetype='text/plain')


if __name__ == '__main__':
    start_http_server(8000)  # Start the Prometheus metrics server
    logger.info('Starting Flask app...')
    app.run(host='0.0.0.0', port=5000)
