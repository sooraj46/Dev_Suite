import json
import threading
import time
import requests
import pika  # RabbitMQ messaging
from abc import ABC, abstractmethod

class BaseAgent(ABC):
    """
    Base class for all agents in the ecosystem.
    Handles capability registration, heartbeats, message listening, and message processing.
    """
    def __init__(self, agent_name, capabilities, registry_url, message_queue_host, queue_name):
        self.agent_name = agent_name
        self.capabilities = capabilities
        self.registry_url = registry_url
        self.message_queue_host = message_queue_host
        self.queue_name = queue_name

        # Register the agent with the capability registry
        self.register_agent()

        # Start heartbeat mechanism in a separate thread
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()

        # Start message listener in a separate thread
        threading.Thread(target=self.listen_for_messages, daemon=True).start()

    def register_agent(self):
        """Registers the agent with the Capability Registry."""
        url = f"{self.registry_url}/register"
        payload = {
            "agent_name": self.agent_name,
            "capabilities": self.capabilities
        }
        try:
            response = requests.post(url, json=payload)
            if response.status_code == 200:
                print(f"[{self.agent_name}] Successfully registered with capabilities: {self.capabilities}")
            else:
                print(f"[{self.agent_name}] Registration failed: {response.text}")
        except requests.exceptions.RequestException as e:
            print(f"[{self.agent_name}] Error registering agent: {e}")

    def send_heartbeat(self):
        """Sends a heartbeat to the Capability Registry to indicate liveness."""
        url = f"{self.registry_url}/heartbeat"
        payload = {"agent_name": self.agent_name}
        try:
            requests.post(url, json=payload)
        except requests.exceptions.RequestException as e:
            print(f"[{self.agent_name}] Heartbeat failed: {e}")

    def heartbeat_loop(self):
        """Continuously sends a heartbeat every 30 seconds."""
        while True:
            self.send_heartbeat()
            time.sleep(30)

    def send_message(self, receiver, message_type, payload):
        """Sends a message to another agent through the message queue."""
        connection = pika.BlockingConnection(pika.ConnectionParameters(host=self.message_queue_host))
        channel = connection.channel()
        channel.queue_declare(queue=self.queue_name, durable=True)

        message = {
            "message_id": str(time.time()),  # Using timestamp as unique ID
            "sender": self.agent_name,
            "receiver": receiver,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "type": message_type,
            "payload": payload
        }

        channel.basic_publish(
            exchange="",
            routing_key=receiver + "Queue",
            body=json.dumps(message),
            properties=pika.BasicProperties(
                delivery_mode=2,  # Make messages persistent
            ),
        )
        connection.close()
        print(f"[{self.agent_name}] Sent message to {receiver}: {message}")

    def listen_for_messages(self):
        """Listens for incoming messages from the message queue and processes them."""
        connection = pika.BlockingConnection(pika.ConnectionParameters(host=self.message_queue_host))
        channel = connection.channel()
        channel.queue_declare(queue=self.queue_name, durable=True)

        def callback(ch, method, properties, body):
            message = json.loads(body)
            print(f"[{self.agent_name}] Received message: {message}")
            self.process_message(message)
            ch.basic_ack(delivery_tag=method.delivery_tag)  # Acknowledge the message

        channel.basic_qos(prefetch_count=1)
        channel.basic_consume(queue=self.queue_name, on_message_callback=callback)

        print(f"[{self.agent_name}] Listening for messages...")
        channel.start_consuming()

    @abstractmethod
    def process_message(self, message):
        """Abstract method that must be implemented by subclasses to handle incoming messages."""
        pass
