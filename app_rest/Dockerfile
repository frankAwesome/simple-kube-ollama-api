# app_rest/Dockerfile
FROM python:3.9-slim

# Set environment variable for RabbitMQ IP
ENV RABBITIP=localhost

# Set the working directory
WORKDIR /app

RUN pip install --upgrade pip
RUN pip install -U Flask
RUN pip install -U flask-cors
RUN pip install pika

# Copy the rest of the application
COPY app_rest.py .

# Expose the port that Flask runs on
EXPOSE 5000

# Run the application
CMD ["python", "app_rest.py"]
