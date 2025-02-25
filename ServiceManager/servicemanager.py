#!/usr/bin/env python
"""
ServiceManager.py

A service responsible for:
  1) Starting and stopping other services
  2) Monitoring health of services
  3) Collecting status of all projects
  4) Resuming projects from their last checkpoint
  5) Communicating with ManagerAgent to continue tasks
"""

import os
import sys
import time
import json
import logging
import requests
import threading
import subprocess
import dotenv
from typing import Dict, List, Any, Optional

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ServiceManager")

# Load environment variables
dotenv.load_dotenv()

# Service endpoints
REGISTRY_URL = os.getenv("REGISTRY_URL", "http://localhost:5005")
FILE_SERVER_BASE_URL = os.getenv("FILE_SERVER_BASE_URL", "http://localhost:6000")
GIT_SERVICE_URL = os.getenv("GIT_SERVICE_URL", "http://localhost:5001")
FRONTEND_SERVICE_URL = os.getenv("FRONTEND_SERVICE_URL", "http://localhost:8080")
MESSAGE_QUEUE_HOST = os.getenv("MESSAGE_QUEUE_HOST", "localhost")

# RabbitMQ connection - we'll use pika for direct messaging
try:
    import pika
    RABBITMQ_AVAILABLE = True
except ImportError:
    logger.warning("RabbitMQ client (pika) not available. Some features may be limited.")
    RABBITMQ_AVAILABLE = False

class ServiceManager:
    """
    ServiceManager class handles service lifecycle and project continuity
    """
    def __init__(self):
        self.services = {
            "agent_registry": {
                "path": "AgentRegistry",
                "script": "agentregistry.py",
                "log_file": "agentregistry.log",
                "process": None,
                "status": "stopped"
            },
            "file_server": {
                "path": "FileServer", 
                "script": "fileserver.py",
                "log_file": "fileserver.log",
                "process": None,
                "status": "stopped"
            },
            "git_service": {
                "path": "GitService",
                "script": "gitservice.py",
                "log_file": "gitservice.log",
                "process": None,
                "status": "stopped"
            },
            "developer_agent": {
                "path": "AgentService",
                "script": "developeragent.py",
                "log_file": "developeragent.log",
                "process": None,
                "status": "stopped"
            },
            "testing_agent": {
                "path": "AgentService",
                "script": "testagent.py",
                "log_file": "testagent.log",
                "process": None,
                "status": "stopped"
            },
            "manager_agent": {
                "path": "AgentService",
                "script": "manageragent.py",
                "log_file": "manageragent.log",
                "process": None,
                "status": "stopped"
            },
            "frontend_service": {
                "path": "FrontendService",
                "script": "frontend_app.py",
                "log_file": "frontend_app.log",
                "process": None,
                "status": "stopped"
            }
        }
        
        self.projects = {}
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logger.info(f"ServiceManager initialized with base directory: {self.base_dir}")

    def start_service(self, service_name: str) -> bool:
        """
        Start a specific service
        
        Args:
            service_name: Name of the service to start
            
        Returns:
            bool: True if started successfully, False otherwise
        """
        if service_name not in self.services:
            logger.error(f"Unknown service: {service_name}")
            return False
            
        service = self.services[service_name]
        if service["status"] == "running":
            logger.info(f"Service {service_name} is already running")
            return True
            
        service_path = os.path.join(self.base_dir, service["path"])
        script_path = os.path.join(service_path, service["script"])
        log_file_path = os.path.join(service_path, service["log_file"])
        
        logger.info(f"Starting {service_name} from {script_path}")
        
        try:
            with open(log_file_path, 'w') as log_file:
                process = subprocess.Popen(
                    [sys.executable, script_path],
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    cwd=service_path
                )
                
            service["process"] = process
            service["status"] = "running"
            service["start_time"] = time.time()
            logger.info(f"Started {service_name} with PID {process.pid}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start {service_name}: {e}")
            return False

    def stop_service(self, service_name: str) -> bool:
        """
        Stop a specific service
        
        Args:
            service_name: Name of the service to stop
            
        Returns:
            bool: True if stopped successfully, False otherwise
        """
        if service_name not in self.services:
            logger.error(f"Unknown service: {service_name}")
            return False
            
        service = self.services[service_name]
        if service["status"] != "running" or service["process"] is None:
            logger.info(f"Service {service_name} is not running")
            return True
            
        try:
            process = service["process"]
            process.terminate()
            
            # Give it a few seconds to terminate gracefully
            for _ in range(5):
                if process.poll() is not None:
                    break
                time.sleep(1)
                
            # Force kill if still running
            if process.poll() is None:
                process.kill()
                process.wait()
                
            service["status"] = "stopped"
            service["process"] = None
            logger.info(f"Stopped {service_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop {service_name}: {e}")
            return False

    def start_all_services(self) -> Dict[str, bool]:
        """
        Start all services in the correct order
        
        Returns:
            Dict[str, bool]: Status of each service start attempt
        """
        # Start services in a specific order to ensure dependencies are met
        service_order = [
            "agent_registry", 
            "file_server", 
            "git_service", 
            "manager_agent",
            "developer_agent",
            "testing_agent",
            "frontend_service"
        ]
        
        results = {}
        for service_name in service_order:
            success = self.start_service(service_name)
            results[service_name] = success
            
            # Give a little time for each service to initialize
            time.sleep(3)
            
            # If critical services fail, abort
            if not success and service_name in ["agent_registry", "file_server"]:
                logger.error(f"Critical service {service_name} failed to start. Aborting.")
                break
                
        return results

    def stop_all_services(self) -> Dict[str, bool]:
        """
        Stop all services in the reverse order
        
        Returns:
            Dict[str, bool]: Status of each service stop attempt
        """
        # Stop in reverse order of importance
        service_order = [
            "frontend_service",
            "developer_agent",
            "testing_agent",
            "manager_agent",
            "git_service",
            "file_server",
            "agent_registry"
        ]
        
        results = {}
        for service_name in service_order:
            success = self.stop_service(service_name)
            results[service_name] = success
            time.sleep(1)
            
        return results

    def check_service_health(self, service_name: str) -> Dict[str, Any]:
        """
        Check the health of a specific service
        
        Args:
            service_name: Name of the service to check
            
        Returns:
            Dict with status information
        """
        if service_name not in self.services:
            return {"status": "unknown", "error": "Unknown service"}
            
        service = self.services[service_name]
        
        # Check process status
        if service["status"] == "running" and service["process"] is not None:
            process = service["process"]
            if process.poll() is None:
                # Process is still running
                # For more detailed health checking, we could add service-specific
                # endpoint pings here
                
                uptime = time.time() - service.get("start_time", time.time())
                return {
                    "status": "running",
                    "pid": process.pid,
                    "uptime_seconds": uptime,
                    "uptime_human": f"{int(uptime // 3600)}h {int((uptime % 3600) // 60)}m {int(uptime % 60)}s"
                }
            else:
                # Process has terminated
                return_code = process.returncode
                service["status"] = "crashed"
                service["process"] = None
                return {
                    "status": "crashed",
                    "return_code": return_code,
                    "error": f"Process terminated with return code {return_code}"
                }
        
        return {"status": service["status"]}

    def check_all_services_health(self) -> Dict[str, Dict[str, Any]]:
        """
        Check health of all services
        
        Returns:
            Dict of service statuses
        """
        results = {}
        for service_name in self.services:
            results[service_name] = self.check_service_health(service_name)
        return results

    def collect_project_statuses(self) -> Dict[str, Dict[str, Any]]:
        """
        Scan the FileServer for all projects and their current status
        
        Returns:
            Dict of projects with their status information
        """
        logger.info("Collecting project statuses from FileServer")
        projects = {}
        
        try:
            # First, get list of all project directories
            list_url = f"{FILE_SERVER_BASE_URL}/list_directory"
            params = {"path": "uploads"}
            resp = requests.get(list_url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            
            project_dirs = [item.replace("[DIR] ", "") for item in data.get("contents", []) if item.startswith("[DIR]")]
            
            # For each project, get the status.md and requirements.md
            for project_name in project_dirs:
                project_info = {"name": project_name, "path": f"uploads/{project_name}"}
                
                # Get status.md
                status_path = f"uploads/{project_name}/status.md"
                status_content = self.fetch_file_from_server(status_path)
                project_info["status"] = status_content or "No status information available."
                
                # Get requirements.md
                req_path = f"uploads/{project_name}/requirements.md"
                req_content = self.fetch_file_from_server(req_path) 
                project_info["requirements"] = req_content or "No requirements information available."
                
                # Check for developmentstatus.md (used by DeveloperAgent)
                dev_status_path = f"uploads/{project_name}/developmentstatus.md"
                dev_status_content = self.fetch_file_from_server(dev_status_path)
                project_info["development_status"] = dev_status_content or ""
                
                # Check for test_results.md (used by TestingAgent)
                test_results_path = f"uploads/{project_name}/test_results.md"
                test_results_content = self.fetch_file_from_server(test_results_path)
                project_info["test_results"] = test_results_content or ""
                
                # Determine current project state
                if "Project marked as completed" in project_info["status"]:
                    project_info["state"] = "completed"
                elif "testing" in project_info["status"].lower():
                    project_info["state"] = "testing"
                elif "generating" in project_info["status"].lower() or "developmentstatus.md" in project_info:
                    project_info["state"] = "development"
                elif "Received TASK_ASSIGNMENT" in project_info["status"]:
                    project_info["state"] = "assigned"
                elif "Project initialized" in project_info["status"]:
                    project_info["state"] = "initialized"
                else:
                    project_info["state"] = "unknown"
                
                projects[project_name] = project_info
                
            self.projects = projects
            logger.info(f"Collected status for {len(projects)} projects")
            return projects
            
        except Exception as e:
            logger.error(f"Error collecting project statuses: {e}")
            return {}

    def fetch_file_from_server(self, file_path: str) -> Optional[str]:
        """
        Reads a file from the File Server, returning its content as a string.
        
        Args:
            file_path: Path to the file on the FileServer
            
        Returns:
            String content or None if error
        """
        url = f"{FILE_SERVER_BASE_URL}/read_file"
        params = {"path": file_path}
        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("content", "")
            else:
                # File may not exist, which is okay
                return None
        except Exception as e:
            logger.error(f"Error fetching file '{file_path}': {e}")
            return None

    def resume_pending_projects(self) -> Dict[str, str]:
        """
        Resume projects that are in a pending state
        
        Returns:
            Dict with status of each resumption attempt
        """
        if not self.projects:
            self.collect_project_statuses()
            
        results = {}
        
        for project_name, project_info in self.projects.items():
            state = project_info.get("state", "unknown")
            
            if state in ["initialized", "assigned", "development", "testing"]:
                logger.info(f"Attempting to resume project {project_name} in state {state}")
                
                # Create payload with project configuration
                project_config = {
                    "project_name": project_name,
                    "file_server_folder": f"uploads/{project_name}",
                    "requirements_md": project_info.get("requirements", ""),
                    "status_md": project_info.get("status", ""),
                    "repo_name": project_name
                }
                
                # Resume based on current state
                if state == "initialized":
                    # The project was just created, send original requirement to manager
                    result = self.send_project_to_manager(
                        msg_type="NEW_REQUIREMENT",
                        project_config=project_config,
                        requirement=project_info.get("requirements", "")
                    )
                    results[project_name] = f"Initialized project resumed: {result}"
                    
                elif state == "development":
                    # The project is in development, continuation message to developer agent
                    continuation_prompt = "Continue development from the current state."
                    result = self.send_project_to_developer(
                        project_config=project_config,
                        prompt=continuation_prompt
                    )
                    results[project_name] = f"Development project resumed: {result}"
                    
                elif state == "testing":
                    # The project is in testing, send test request to testing agent
                    result = self.send_project_to_tester(
                        project_config=project_config,
                        run_pytest=True
                    )
                    results[project_name] = f"Testing project resumed: {result}"
                    
                else:  # "assigned" or other states
                    # Generic resume through manager
                    result = self.send_project_to_manager(
                        msg_type="RESUME_PROJECT",
                        project_config=project_config,
                        requirement=project_info.get("requirements", "")
                    )
                    results[project_name] = f"Other project resumed via manager: {result}"
            
            else:
                results[project_name] = f"No action needed, state is {state}"
                
        return results

    def send_project_to_manager(self, msg_type: str, project_config: Dict[str, Any], requirement: str) -> str:
        """
        Send a project to the ManagerAgent
        
        Args:
            msg_type: Message type for the manager (NEW_REQUIREMENT, RESUME_PROJECT, etc)
            project_config: Project configuration dictionary
            requirement: The requirement text
            
        Returns:
            Status string
        """
        if not RABBITMQ_AVAILABLE:
            return "RabbitMQ not available for messaging"
            
        try:
            connection = pika.BlockingConnection(pika.ConnectionParameters(host=MESSAGE_QUEUE_HOST))
            channel = connection.channel()
            queue_name = "ManagerAgentQueue"
            channel.queue_declare(queue=queue_name, durable=True)
            
            payload = {
                "requirement": requirement,
                "priority": "medium",
                "project_config": project_config
            }
            
            message = {
                "message_id": str(time.time()),
                "sender": "ServiceManager",
                "receiver": "ManagerAgent",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "type": msg_type,
                "payload": payload
            }
            
            channel.basic_publish(
                exchange="",
                routing_key=queue_name,
                body=json.dumps(message),
                properties=pika.BasicProperties(delivery_mode=2),
            )
            connection.close()
            
            logger.info(f"Sent {msg_type} message to ManagerAgent for project {project_config['project_name']}")
            return "Success"
            
        except Exception as e:
            logger.error(f"Error sending message to ManagerAgent: {e}")
            return f"Failed: {str(e)}"

    def send_project_to_developer(self, project_config: Dict[str, Any], prompt: str) -> str:
        """
        Send a project directly to the DeveloperAgent
        
        Args:
            project_config: Project configuration dictionary
            prompt: The development prompt
            
        Returns:
            Status string
        """
        if not RABBITMQ_AVAILABLE:
            return "RabbitMQ not available for messaging"
            
        try:
            connection = pika.BlockingConnection(pika.ConnectionParameters(host=MESSAGE_QUEUE_HOST))
            channel = connection.channel()
            queue_name = "DeveloperAgentQueue"
            channel.queue_declare(queue=queue_name, durable=True)
            
            payload = {
                "prompt": prompt,
                "project_config": project_config,
                "assigned_by": "ServiceManager",
                "reason": "Project resumption"
            }
            
            message = {
                "message_id": str(time.time()),
                "sender": "ServiceManager",
                "receiver": "DeveloperAgent",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "type": "TASK_ASSIGNMENT",
                "payload": payload
            }
            
            channel.basic_publish(
                exchange="",
                routing_key=queue_name,
                body=json.dumps(message),
                properties=pika.BasicProperties(delivery_mode=2),
            )
            connection.close()
            
            logger.info(f"Sent TASK_ASSIGNMENT to DeveloperAgent for project {project_config['project_name']}")
            return "Success"
            
        except Exception as e:
            logger.error(f"Error sending message to DeveloperAgent: {e}")
            return f"Failed: {str(e)}"

    def send_project_to_tester(self, project_config: Dict[str, Any], run_pytest: bool = True) -> str:
        """
        Send a project to the TestingAgent
        
        Args:
            project_config: Project configuration dictionary
            run_pytest: Whether to run pytest
            
        Returns:
            Status string
        """
        if not RABBITMQ_AVAILABLE:
            return "RabbitMQ not available for messaging"
            
        try:
            connection = pika.BlockingConnection(pika.ConnectionParameters(host=MESSAGE_QUEUE_HOST))
            channel = connection.channel()
            queue_name = "TestingAgentQueue"
            channel.queue_declare(queue=queue_name, durable=True)
            
            # Testing request
            payload = {
                "project_config": project_config,
                "run_pytest": run_pytest,
                "test_results_file": "test_results.md",
                "commit_message": "Test results from resumed testing"
            }
            
            message = {
                "message_id": str(time.time()),
                "sender": "ServiceManager",
                "receiver": "TestingAgent",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "type": "TEST_REQUEST",
                "payload": payload
            }
            
            channel.basic_publish(
                exchange="",
                routing_key=queue_name,
                body=json.dumps(message),
                properties=pika.BasicProperties(delivery_mode=2),
            )
            connection.close()
            
            logger.info(f"Sent TEST_REQUEST to TestingAgent for project {project_config['project_name']}")
            return "Success"
            
        except Exception as e:
            logger.error(f"Error sending message to TestingAgent: {e}")
            return f"Failed: {str(e)}"

    def run_service_monitor(self, interval: int = 60):
        """
        Start a background thread to monitor service health
        
        Args:
            interval: Check interval in seconds
        """
        def monitor_func():
            while True:
                try:
                    health_statuses = self.check_all_services_health()
                    
                    # Auto-restart any crashed services
                    for service_name, health in health_statuses.items():
                        if health["status"] == "crashed":
                            logger.warning(f"Service {service_name} has crashed. Attempting restart.")
                            self.start_service(service_name)
                    
                    time.sleep(interval)
                except Exception as e:
                    logger.error(f"Error in service monitor: {e}")
                    time.sleep(interval)
        
        thread = threading.Thread(target=monitor_func, daemon=True)
        thread.start()
        logger.info(f"Service monitor started with {interval}s interval")
        return thread

def main():
    """
    Main entry point for the service manager
    """
    logger.info("Starting ServiceManager")
    manager = ServiceManager()
    
    # Start all services
    logger.info("Starting all services...")
    start_results = manager.start_all_services()
    for service, result in start_results.items():
        logger.info(f"Service {service}: {'Started' if result else 'Failed'}")
    
    # Give services time to start up
    logger.info("Waiting for services to start up...")
    time.sleep(15)
    
    # Check service health
    health = manager.check_all_services_health()
    for service, status in health.items():
        if status["status"] == "running":
            logger.info(f"Service {service} is running with PID {status.get('pid', 'unknown')}")
        else:
            logger.warning(f"Service {service} status: {status['status']}")
    
    # Collect project statuses
    logger.info("Collecting project statuses...")
    projects = manager.collect_project_statuses()
    logger.info(f"Found {len(projects)} projects")
    
    # Resume any pending projects
    if projects:
        logger.info("Resuming pending projects...")
        resume_results = manager.resume_pending_projects()
        for project, result in resume_results.items():
            logger.info(f"Project {project}: {result}")
    
    # Start monitoring thread
    monitor_thread = manager.run_service_monitor(interval=120)  # Check every 2 minutes
    
    # Keep the application running until Ctrl+C
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping all services...")
        stop_results = manager.stop_all_services()
        for service, result in stop_results.items():
            logger.info(f"Service {service}: {'Stopped' if result else 'Failed to stop'}")
        logger.info("ServiceManager terminated")

if __name__ == "__main__":
    main()