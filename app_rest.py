import pika
import uuid
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import os
import psutil
from prometheus_client import start_http_server, Summary, Gauge, Counter, generate_latest

app = Flask(__name__)
CORS(app)  # This will enable CORS for all routes

# Prometheus metrics
REQUEST_TIME = Summary('request_processing_seconds', 'Time spent processing request')
CPU_USAGE = Gauge('cpu_usage', 'CPU usage')
MEMORY_USAGE = Gauge('memory_usage', 'Memory usage')
REQUEST_COUNTER = Counter('http_requests_total', 'Total number of HTTP requests')


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

    def on_response(self, ch, method, props, body):
        if self.corr_id == props.correlation_id:
            self.response = body

    @REQUEST_TIME.time()  # Measure the time taken by the call method
    def call(self, n):
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
        return self.response.decode()


@app.route('/prompt_llm', methods=['POST'])
def prompt_llm():
    REQUEST_COUNTER.inc()  # Increment the request counter
    data = request.get_json()
    rabbitmq = RabbitMQClient()
    response = rabbitmq.call(data)
    return jsonify({"response": response})


@app.route('/metrics', methods=['GET'])
def metrics():
    cpu_usage = psutil.cpu_percent()
    memory_usage = psutil.virtual_memory().percent
    CPU_USAGE.set(cpu_usage)
    MEMORY_USAGE.set(memory_usage)
    return Response(generate_latest(), mimetype='text/plain')


if __name__ == '__main__':
    start_http_server(8000)  # Start the Prometheus metrics server
    app.run(host='127.0.0.1', port=5000)
