import os
import sys
import time
import json
import logging
import requests
import re
import dotenv
import google.genai as genai
import google.genai.types as types

from baseservice import BaseAgent

# Load environment variables
dotenv.load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ManagerAgent")

API_KEY = os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    logger.error("GOOGLE_API_KEY environment variable not set.")
    sys.exit(1)

REGISTRY_URL = os.getenv("REGISTRY_URL", "http://localhost:5005")
FILE_SERVER_BASE_URL = os.getenv("FILE_SERVER_BASE_URL", "http://localhost:6000")
GIT_SERVICE_URL = os.getenv("GIT_SERVICE_URL", "http://localhost:5001")
MESSAGE_QUEUE_HOST = os.getenv("MESSAGE_QUEUE_HOST", "localhost")
QUEUE_NAME = os.getenv("MANAGER_QUEUE", "ManagerAgentQueue")

# Add a new environment variable for the Frontend Service URL.
FRONTEND_SERVICE_URL = os.getenv("FRONTEND_SERVICE_URL", "http://localhost:8080")

USING_GOOGLE_GENAI = True


def list_agents_from_registry() -> dict:
    """
    Fetch the list of agents and their capabilities from the Capability Registry.
    Returns a dict like: { "DeveloperAgent1": ["code_implementation"], "TestingAgent1": ["testing"], ...}
    """
    try:
        url = f"{REGISTRY_URL}/list_agents"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()  # Expected shape: { agent_name: [capabilities], ...}
        return data
    except Exception as e:
        logger.exception("[ManagerAgent] Could not fetch agent list from registry.")
        return {}


def read_file_from_server(path: str) -> str:
    """
    Reads a file from the File Server, returning its contents as a string.
    Returns an empty string if the file doesn't exist or there's an error.
    """
    try:
        url = f"{FILE_SERVER_BASE_URL}/read_file"
        params = {"path": path}
        resp = requests.get(url, params=params, timeout=10)
        
        if resp.status_code == 404:
            # File doesn't exist yet - this is expected in some cases
            logger.info(f"[ManagerAgent] File not found on server (normal): {path}")
            return ""
            
        resp.raise_for_status()  # Only raise for other status codes
        data = resp.json()
        return data.get("content", "")
    except requests.exceptions.HTTPError as http_err:
        logger.error(f"[ManagerAgent] Error reading file from server: {path} - {http_err}")
        return ""
    except Exception as e:
        logger.exception(f"[ManagerAgent] Error reading file from server: {path}")
        return ""


def write_file_to_server(path: str, content: str):
    """
    Writes/overwrites a file on the File Server.
    """
    try:
        url = f"{FILE_SERVER_BASE_URL}/write_file"
        payload = {"path": path, "content": content}
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info(f"[ManagerAgent] Wrote file to server: {path}")
    except Exception as e:
        logger.exception(f"[ManagerAgent] Error writing file to server: {path}")


def init_git_repo(repo_name: str):
    """
    Initialize a bare Git repo on the Git Service.
    """
    try:
        url = f"{GIT_SERVICE_URL}/init"
        payload = {"repo_name": repo_name}
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info(f"[ManagerAgent] Initialized Git repository: {repo_name}")
    except Exception as e:
        logger.exception(f"[ManagerAgent] Git init failed for {repo_name}")


def ask_llm_for_action(
    new_request: str,
    requirements_md: str,
    status_md: str,
    agents_and_caps: dict
) -> dict:
    """
    Use the LLM to decide if clarifications are needed or which agent to assign a task to.
    The LLM sees:
      - The current project requirements (requirements.md)
      - The current status (status.md)
      - A new or updated request from the user
      - The available agent capabilities from the registry

    We request a JSON structure:
    {
        "action": "clarification" | "assign_task",
        "clarifications": ["..."],
        "selected_agent": "AgentName",
        "capability_required": "some_capability",
        "reason": "brief explanation"
    }

    If action="clarification", we expect an array of clarifications.
    If action="assign_task", we expect a selected_agent + capability_required.
    """
    agent_list_str = json.dumps(agents_and_caps, indent=2)

    prompt = f"""
You are an advanced manager in a multi-agent ecosystem. You have:
1) The project requirements (requirements.md):
---\n{requirements_md}\n---

2) The project status (status.md):
---\n{status_md}\n---

3) A new or updated request from the user:
---\n{new_request}\n---

4) A list of currently available agents and their capabilities:
{agent_list_str}

Decide if we need clarifications or if we should assign a task to one of these agents.
If clarifications are needed, set "action" to "clarification" and provide a list of questions.
Otherwise, pick exactly one existing agent from the above list, specify the relevant capability
(this must be a capability from the agent's capabilities list), and set "action" to "assign_task".

Output valid JSON in the exact format below (no additional keys):
{{
  "action": "clarification" | "assign_task",
  "clarifications": ["..."],
  "selected_agent": "AgentName",
  "capability_required": "some_capability",
  "reason": "brief explanation"
}}
""".strip()

    try:
        client = genai.Client(api_key=API_KEY)
        response = client.models.generate_content(
            model="gemini-2.0-flash-thinking-exp-01-21",
            contents=[types.Part.from_text(text=prompt)]
        )
        logger.info(f"[ManagerAgent] LLM raw response:\n{response.text}\n")
        raw_response = response.text

        # Find the position of the first '{'
        json_start = raw_response.find('{')
        # Find the position of the last '}'
        json_end = raw_response.rfind('}')

        if json_start != -1 and json_end != -1 and json_start < json_end:
            # Slice out everything from '{' through '}'
            raw_response = raw_response[json_start : json_end + 1]

        result = json.loads(raw_response)
        return result
    except Exception as e:
        logger.exception("[ManagerAgent] LLM call failed or invalid JSON.")
        return {
            "action": "clarification",
            "clarifications": [
                "LLM error or invalid response, please clarify.",
            ],
            "selected_agent": "",
            "capability_required": "",
            "reason": "LLM fallback"
        }


def ask_llm_after_task_execution(
    task_execution_payload: dict,
    requirements_md: str,
    status_md: str,
    agents_and_caps: dict
) -> dict:
    """
    After receiving a TASK_EXECUTION result, ask the LLM what to do next.
    The LLM sees the project requirements, current status, the developer's result,
    and the list of available agents. The LLM decides whether the project is now complete
    or if we should assign the next task to some agent.

    Expected JSON structure in response:
    {
      "action": "project_completed" | "assign_task",
      "selected_agent": "AgentName" | "",
      "capability_required": "some_capability" | "",
      "reason": "brief explanation"
    }

    If action="project_completed", the manager should mark the project done.
    If action="assign_task", the manager picks the agent from 'selected_agent'.
    """
    agent_list_str = json.dumps(agents_and_caps, indent=2)
    task_result_str = json.dumps(task_execution_payload, indent=2)

    prompt = f"""
You are an advanced manager in a multi-agent ecosystem. You have:
1) The project requirements (requirements.md):
---\n{requirements_md}\n---

2) The project status (status.md):
---\n{status_md}\n---

3) A result from the DeveloperAgent (or other agent) indicating a task execution outcome:
---\n{task_result_str}\n---

4) A list of currently available agents and their capabilities:
{agent_list_str}

Decide if the project is now complete or if we need to assign another task to an agent.
If the project is finished, set "action" to "project_completed".
Otherwise, set "action" to "assign_task" and pick exactly one agent from the above list,
with a capability that suits the next step.

Output valid JSON in the exact format below (no additional keys):
{{
  "action": "project_completed" | "assign_task",
  "selected_agent": "AgentName",
  "capability_required": "some_capability",
  "reason": "brief explanation"
}}
""".strip()

    try:
        client = genai.Client(api_key=API_KEY)
        response = client.models.generate_content(
            model="gemini-2.0-flash-thinking-exp-01-21",
            contents=[types.Part.from_text(text=prompt)]
        )
        logger.info(f"[ManagerAgent] LLM post-task prompt:\n{prompt}\n")
        logger.info(f"[ManagerAgent] LLM post-task response:\n{response.text}\n")
        raw_response = response.text

        # Find the position of the first '{'
        json_start = raw_response.find('{')
        # Find the position of the last '}'
        json_end = raw_response.rfind('}')

        if json_start != -1 and json_end != -1 and json_start < json_end:
            # Slice out everything from '{' through '}'
            raw_response = raw_response[json_start : json_end + 1]

        result = json.loads(raw_response)
        return result
    except Exception as e:
        logger.exception("[ManagerAgent] LLM call failed or invalid JSON in post-task.")
        return {
            "action": "project_completed",  # fallback
            "selected_agent": "",
            "capability_required": "",
            "reason": "LLM fallback: error"
        }


def create_project_in_fileserver(requirement_text: str) -> dict:
    """
    Creates a new set of files on the File Server: requirements.md, status.md.
    Also initializes a new Git repo. Returns details.
    """
    project_name = f"project_{int(time.time())}"
    file_server_folder = f"uploads/{project_name}"

    # Initialize Git repo
    init_git_repo(project_name)

    # Write requirements.md
    req_path = f"{file_server_folder}/requirements.md"
    write_file_to_server(req_path, requirement_text)

    # Write status.md
    status_path = f"{file_server_folder}/status.md"
    initial_status = "Project initialized."
    write_file_to_server(status_path, initial_status)

    return {
        "project_name": project_name,
        "file_server_folder": file_server_folder,
        "requirements_path": req_path,
        "status_path": status_path
    }


def forward_message_to_frontend(message_type: str, payload: dict, progress: float = None):
    """
    Forwards messages from agent services to the Frontend Service. This supports
    both task execution results and progress updates.
    
    Args:
        message_type (str): Type of message being forwarded (e.g., "TASK_EXECUTION", "PROGRESS_UPDATE")
        payload (dict): The message payload
        progress (float, optional): Progress indicator for tasks (0.0 to 1.0)
    """
    try:
        url = f"{FRONTEND_SERVICE_URL}/receive_task_execution"
        
        # Create the complete message with all required fields
        message = {
            "message_id": str(time.time()),  # Using timestamp as unique ID
            "sender": "ManagerAgent",
            "receiver": "FrontendUI",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "type": message_type,
            "payload": payload,
            "progress": progress
        }
        
        # Use shorter timeout to avoid blocking and add more retries
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                response = requests.post(url, json=message, timeout=5)
                response.raise_for_status()
                logger.info(f"[ManagerAgent] Successfully forwarded {message_type} to Frontend. Response: {response.text}")
                return
            except requests.exceptions.ConnectionError:
                # Frontend might be starting up, retry after a short delay
                retry_count += 1
                if retry_count < max_retries:
                    logger.info(f"[ManagerAgent] Frontend connection failed, retrying ({retry_count}/{max_retries})...")
                    time.sleep(2)  # Short delay before retry
                else:
                    logger.warning(f"[ManagerAgent] Frontend not available after {max_retries} retries. Message {message_type} not delivered.")
                    return
            except Exception as e:
                # For other errors, just log and continue
                logger.exception(f"[ManagerAgent] Error sending {message_type} to Frontend: {e}")
                return
    except Exception as e:
        logger.exception(f"[ManagerAgent] Failed to forward {message_type} to Frontend: {e}")


def post_clarification_request_to_frontend(
    project_name: str,
    requirement: str,
    clarifications: list,
    reason: str
):
    """
    Post a clarification request to the frontend's /receive_clarification_request endpoint.
    Pass along project_name (to identify the project), the requirement text, clarifications,
    and a short reason.
    """
    payload = {
        "project_name": project_name,
        "requirement": requirement,
        "clarifications": clarifications,
        "reason": reason
    }
    try:
        endpoint = f"{FRONTEND_SERVICE_URL}/receive_clarification_request"
        resp = requests.post(endpoint, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info(f"[ManagerAgent] Clarification request posted to frontend for project {project_name}.")
    except Exception as e:
        logger.exception(f"[ManagerAgent] Failed to post clarification request to frontend: {e}")

class ManagerAgent(BaseAgent):
    """
    ManagerAgent that:
      - Retrieves available agents & capabilities from Capability Registry
      - Fetches project files from File Server
      - Calls LLM to decide next action
      - Either requests clarifications or assigns a task to a chosen agent (no if-else on agent name)
      - Processes TASK_EXECUTION messages received from DeveloperAgent and forwards them to the Frontend.
      - Now also calls a new LLM method after task execution to see if project is complete or if another agent must be assigned.
    """

    def __init__(self, agent_name: str, registry_url: str, message_queue_host: str, queue_name: str):
        super().__init__(
            agent_name=agent_name,
            capabilities=["task_management", "dynamic_decision"],
            registry_url=registry_url,
            message_queue_host=message_queue_host,
            queue_name=queue_name
        )
        logger.info(f"[{self.agent_name}] ManagerAgent initialized.")

    def process_message(self, message: dict):
        try:
            msg_type = message.get("type", "")
            payload = message.get("payload", {})

            if msg_type in ["NEW_REQUIREMENT", "UPDATE_REQUIREMENT", "CLARIFICATION_RESPONSE"]:
                # Merge requirement + clarification
                new_request = payload.get("requirement", "")
                if payload.get("clarification"):
                    new_request += f"\n\nAdditional Clarification:\n{payload['clarification']}"

                logger.info(f"[{self.agent_name}] Received requirement/update: {new_request}")

                # 1) Get agent list from registry
                agents_and_caps = list_agents_from_registry()
                
                # Check if project_config is provided in the payload
                project_config = payload.get("project_config", None)
                
                if project_config and msg_type in ["UPDATE_REQUIREMENT", "CLARIFICATION_RESPONSE"]:
                    # Use existing project from the config
                    project_details = {
                        "project_name": project_config.get("project_name"),
                        "file_server_folder": project_config.get("file_server_folder"),
                        "requirements_path": f"{project_config.get('file_server_folder')}/requirements.md",
                        "status_path": f"{project_config.get('file_server_folder')}/status.md"
                    }
                    
                    # For updates, we should update the existing requirements file
                    if msg_type == "UPDATE_REQUIREMENT":
                        # Fetch current requirements and append the update
                        current_req = read_file_from_server(project_details["requirements_path"])
                        updated_req = current_req + f"\n\n## Update {time.strftime('%Y-%m-%d %H:%M:%S')}:\n{new_request}"
                        write_file_to_server(project_details["requirements_path"], updated_req)
                else:
                    # Create a new project for NEW_REQUIREMENT
                    project_details = create_project_in_fileserver(new_request)

                # 2) Fetch the newly written files from file server
                req_content = read_file_from_server(project_details["requirements_path"])
                status_content = read_file_from_server(project_details["status_path"])

                # 3) Ask LLM how to proceed
                llm_result = ask_llm_for_action(
                    new_request=new_request,
                    requirements_md=req_content,
                    status_md=status_content,
                    agents_and_caps=agents_and_caps
                )

                action = llm_result.get("action", "clarification")
                clarifications = llm_result.get("clarifications", [])
                selected_agent = llm_result.get("selected_agent", "")
                capability_req = llm_result.get("capability_required", "")
                reason = llm_result.get("reason", "")

                if action == "clarification":
                    # Send clarifications request to the FRONTEND, so the user can respond
                    logger.info(f"[{self.agent_name}] Requesting clarification: {clarifications}")
                    post_clarification_request_to_frontend(
                        project_name=project_details["project_name"],
                        requirement=new_request,
                        clarifications=clarifications,
                        reason=reason
                    )

                elif action == "assign_task" and selected_agent:
                    # We have an agent selected by LLM
                    logger.info(f"[{self.agent_name}] LLM selected {selected_agent} with capability {capability_req}")
                    
                    # Update status.md with the task assignment
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                    updated_status = status_content + f"\n\n{timestamp} - Task assigned to {selected_agent} with capability {capability_req}."
                    write_file_to_server(project_details["status_path"], updated_status)
                    
                    # Use the updated status in the project config
                    task_payload = {
                        "prompt": f"Task assigned via LLM. Requirement:\n{new_request}",
                        "project_config": {
                            "project_name": project_details["project_name"],
                            "file_server_folder": project_details["file_server_folder"],
                            "requirements_md": req_content,
                            "status_md": updated_status,  # Use updated status
                            "repo_name": project_details["project_name"]
                        },
                        "assigned_by": self.agent_name,
                        "reason": reason,
                    }

                    self.send_message(
                        receiver=selected_agent,
                        message_type="TASK_ASSIGNMENT",
                        payload=task_payload
                    )
                else:
                    # Fallback if no agent was chosen or unknown action
                    logger.warning(f"[{self.agent_name}] Invalid action or agent. Asking for default clarification.")
                    post_clarification_request_to_frontend(
                        project_name=project_details["project_name"],
                        requirement=new_request,
                        clarifications=["No valid agent selected by LLM."],
                        reason="Fallback"
                    )

            elif msg_type == "PROGRESS_UPDATE":
                # Process progress update messages from agents
                logger.info(f"[{self.agent_name}] Received PROGRESS_UPDATE from {message.get('sender', 'unknown')}")
                
                # Forward progress updates to the frontend
                progress = message.get("progress", 0.0)
                forward_message_to_frontend("PROGRESS_UPDATE", payload, progress)
                
            elif msg_type == "TASK_EXECUTION":
                # Process TASK_EXECUTION message from DeveloperAgent
                logger.info(f"[{self.agent_name}] Received TASK_EXECUTION message from DeveloperAgent with payload: {payload}")

                # 1) Forward the task execution result to the Frontend Service
                forward_message_to_frontend("TASK_EXECUTION", payload)

                # 2) Attempt to read the project_config from the payload to identify the project paths
                code_generation_status = payload.get("code_generation_status", "failure")
                project_config = payload.get("project_config", {})
                file_server_folder = project_config.get("file_server_folder", "")
                requirements_md_path = os.path.join(file_server_folder, "requirements.md")
                status_md_path = os.path.join(file_server_folder, "status.md")

                # 3) Read the latest requirements and status
                requirements_content = read_file_from_server(requirements_md_path)
                status_content = read_file_from_server(status_md_path)

                # 4) Get the list of agents and capabilities
                agents_and_caps = list_agents_from_registry()

                # 5) Ask the LLM if project is completed or if we should assign another task
                llm_decision = ask_llm_after_task_execution(
                    task_execution_payload=payload,
                    requirements_md=requirements_content,
                    status_md=status_content + f"\n\nTask execution status: {code_generation_status}",
                    agents_and_caps=agents_and_caps
                )

                next_action = llm_decision.get("action", "project_completed")
                selected_agent = llm_decision.get("selected_agent", "")
                capability_req = llm_decision.get("capability_required", "")
                reason = llm_decision.get("reason", "")

                if next_action == "project_completed":
                    # Update status.md to mark project complete
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                    updated_status = status_content + f"\n\n{timestamp} - Project marked as completed by ManagerAgent (LLM decision)."
                    write_file_to_server(status_md_path, updated_status)
                    logger.info(f"[{self.agent_name}] Marked project as completed in status file.")

                elif next_action == "assign_task" and selected_agent:
                    logger.info(f"[{self.agent_name}] LLM decided next task for {selected_agent} with capability {capability_req}")
                    
                    # Update status.md with the task assignment
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                    updated_status = status_content + f"\n\n{timestamp} - Assigned next task to {selected_agent} with capability {capability_req}."
                    write_file_to_server(status_md_path, updated_status)
                    
                    next_task_payload = {
                        "prompt": f"Next task assigned via post-task LLM decision.",
                        "project_config": project_config,  # reuse the existing project_config
                        "assigned_by": self.agent_name,
                        "reason": reason
                    }
                    self.send_message(
                        receiver=selected_agent,
                        message_type="TASK_ASSIGNMENT",
                        payload=next_task_payload
                    )
                else:
                    # If we are missing an agent or something else, just mark as complete for now
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                    fallback_status = status_content + f"\n\n{timestamp} - No valid next step from LLM. Marking project completed."
                    write_file_to_server(status_md_path, fallback_status)
                    logger.warning(f"[{self.agent_name}] No valid next action from LLM. Marking project completed.")

            else:
                logger.info(f"[{self.agent_name}] Ignoring message type: {msg_type}")
        except Exception as e:
            logger.exception(f"[{self.agent_name}] Exception while processing message: {e}")
            error_payload = {
                "status": "failure",
                "error": str(e)
            }
            self.send_message(
                receiver=message.get("sender", "Client"),
                message_type="STATUS_UPDATE",
                payload=error_payload
            )

def main():
    AGENT_NAME = "ManagerAgent"
    manager_agent = ManagerAgent(
        agent_name=AGENT_NAME,
        registry_url=REGISTRY_URL,
        message_queue_host=MESSAGE_QUEUE_HOST,
        queue_name=QUEUE_NAME
    )

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info(f"[{AGENT_NAME}] Shutting down.")


if __name__ == "__main__":
    main()
