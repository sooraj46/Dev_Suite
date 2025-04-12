from AgentService.baseservice import BaseService
import docker
import logging
import os

class DockerAgent(BaseService):
    def __init__(self):
        super().__init__()
        self.client = docker.from_env()
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)

    def build_image(self, dockerfile, tag):
        try:
            image, build_logs = self.client.images.build(dockerfile=dockerfile, tag=tag)
            for line in build_logs:
                if 'stream' in line:
                    print(line['stream'].strip())
            self.logger.info(f"Successfully built image: {image.id}")
            return image.id
        except docker.errors.APIError as e:
            self.logger.error(f"Error building image: {e}")
            return None

    def run_container(self, image_tag, container_name=None):
        try:
            container = self.client.containers.run(image_tag, name=container_name, detach=True)
            self.logger.info(f"Started container: {container.id} from image: {image_tag}")
            return container.id
        except docker.errors.ImageNotFound as e:
            self.logger.error(f"Image not found: {e}")
            return None
        except docker.errors.APIError as e:
            self.logger.error(f"Error running container: {e}")
            return None

    def pull_image(self, image_tag):
        try:
            self.client.images.pull(image_tag)
            self.logger.info(f"Successfully pulled image: {image_tag}")
            return True
        except docker.errors.APIError as e:
            self.logger.error(f"Error pulling image: {e}")
            return False

    def push_image(self, image_tag):
        try:
            self.client.images.push(image_tag)
            self.logger.info(f"Successfully pushed image: {image_tag}")
            return True
        except docker.errors.APIError as e:
            self.logger.error(f"Error pushing image: {e}")
            return False

    def list_images(self):
        try:
            images = self.client.images.list()
            return [image.tags for image in images]
        except docker.errors.APIError as e:
            self.logger.error(f"Error listing images: {e}")
            return []

    def list_containers(self):
        try:
            containers = self.client.containers.list()
            return [container.name for container in containers]
        except docker.errors.APIError as e:
            self.logger.error(f"Error listing containers: {e}")
            return []

    def create_network(self, network_name):
        try:
            network = self.client.networks.create(network_name, driver="bridge")
            self.logger.info(f"Created network: {network.id}")
            return network.id
        except docker.errors.APIError as e:
            self.logger.error(f"Error creating network: {e}")
            return None

    def list_networks(self):
        try:
            networks = self.client.networks.list()
            return [network.name for network in networks]
        except docker.errors.APIError as e:
            self.logger.error(f"Error listing networks: {e}")
            return []

    def delete_network(self, network_name):
        try:
            network = self.client.networks.get(network_name)
            network.remove()
            self.logger.info(f"Deleted network: {network_name}")
            return True
        except docker.errors.APIError as e:
            self.logger.error(f"Error deleting network: {e}")
            return False

    def create_volume(self, volume_name):
        try:
            volume = self.client.volumes.create(volume_name)
            self.logger.info(f"Created volume: {volume.name}")
            return volume.name
        except docker.errors.APIError as e:
            self.logger.error(f"Error creating volume: {e}")
            return None

    def list_volumes(self):
        try:
            volumes = self.client.volumes.list()
            return [volume.name for volume in volumes]
        except docker.errors.APIError as e:
            self.logger.error(f"Error listing volumes: {e}")
            return []

    def delete_volume(self, volume_name):
        try:
            volume = self.client.volumes.get(volume_name)
            volume.remove()
            self.logger.info(f"Deleted volume: {volume_name}")
            return True
        except docker.errors.APIError as e:
            self.logger.error(f"Error deleting volume: {e}")
            return False

    def run_docker_compose(self, compose_file_path):
        try:
            project_name = os.path.basename(os.path.dirname(compose_file_path))
            os.chdir(os.path.dirname(compose_file_path))  # Change to the directory containing the compose file
            result = os.system(f"docker-compose -p {project_name} up -d")
            os.chdir("-")  # Change back to initial dir. Consider storing the initial dir in init for robustness
            if result == 0:
                self.logger.info(f"Started Docker Compose project: {project_name} from {compose_file_path}")
                return True
            else:
                self.logger.error(f"Error starting Docker Compose project: {project_name}")
                return False
        except Exception as e: #Catching a broader exception as os.system can fail in various ways
            self.logger.error(f"Error running docker-compose: {e}")
            return False

    def stop_docker_compose(self, compose_file_path):
        try:
            project_name = os.path.basename(os.path.dirname(compose_file_path))
            os.chdir(os.path.dirname(compose_file_path))  # Change to the directory containing the compose file
            result = os.system(f"docker-compose -p {project_name} down")
            os.chdir("-")  # Change back to initial dir. Consider storing the initial dir in init for robustness
            if result == 0:
                self.logger.info(f"Stopped Docker Compose project: {project_name} from {compose_file_path}")
                return True
            else:
                self.logger.error(f"Error stopping Docker Compose project: {project_name}")
                return False
        except Exception as e: #Catching a broader exception as os.system can fail in various ways
            self.logger.error(f"Error running docker-compose down: {e}")
            return False
            
    def monitor_container(self, container_id):
        try:
            container = self.client.containers.get(container_id)
            return container.status
        except docker.errors.NotFound as e:
            self.logger.error(f"Container not found: {e}")
            return None
        except docker.errors.APIError as e:
            self.logger.error(f"Error monitoring container: {e}")
            return None

    def process_message(self, message):
        if isinstance(message, dict) and "command" in message:
            command = message["command"]
            if command == "build_image":
                return self.build_image(message.get("dockerfile"), message.get("tag"))
            elif command == "run_container":
                return self.run_container(message.get("image_tag"), message.get("container_name"))
            elif command == "pull_image":
                return self.pull_image(message.get("image_tag"))
            elif command == "push_image":
                return self.push_image(message.get("image_tag"))
            elif command == "list_images":
                return self.list_images()
            elif command == "list_containers":
                return self.list_containers()
            elif command == "create_network":
                return self.create_network(message.get("network_name"))
            elif command == "list_networks":
                return self.list_networks()
            elif command == "delete_network":
                return self.delete_network(message.get("network_name"))
            elif command == "create_volume":
                return self.create_volume(message.get("volume_name"))
            elif command == "list_volumes":
                return self.list_volumes()
            elif command == "delete_volume":
                return self.delete_volume(message.get("volume_name"))
            elif command == "run_docker_compose":
                return self.run_docker_compose(message.get("compose_file_path"))
            elif command == "stop_docker_compose":
                return self.stop_docker_compose(message.get("compose_file_path"))
            elif command == "monitor_container":
                return self.monitor_container(message.get("container_id"))
            else:
                self.logger.warning(f"Unknown command: {command}")
                print("Unknown command.")
                return None  # or perhaps return an "error" status
        else:
            self.logger.warning(f"Invalid message format: {message}")
            print("Unknown command.")
            return None  # or perhaps return an "error" status