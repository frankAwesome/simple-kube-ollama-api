# app_node/Dockerfile
FROM python:3.9-slim

# Set environment variable for RabbitMQ IP
ENV RABBITIP=localhost

# Set the working directory
WORKDIR /app

RUN pip install --upgrade pip
RUN pip install pika
RUN pip install requests
RUN pip install kafka-python-ng
RUN pip install fluent-logger
RUN pip install prometheus_client

# Copy the rest of the application
COPY app_node.py .

# Run the application
CMD ["python", "app_node.py"]
