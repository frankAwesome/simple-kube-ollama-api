# app_rest.py
import pika
import uuid
from flask import Flask, request, jsonify
from flask_cors import CORS
import os

app = Flask(__name__)
CORS(app)  # This will enable CORS for all routes

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
    data = request.get_json()
    rabbitmq = RabbitMQClient()
    response = rabbitmq.call(data)
    return jsonify({"response": response})


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000)
