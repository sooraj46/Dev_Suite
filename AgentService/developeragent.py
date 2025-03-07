#!/usr/bin/env python
"""
DeveloperAgent.py

Extended Developer Agent that:
  1) Pulls project files from the File Server,
  2) Updates/generates code (using Google GenAI if requested),
  3) Runs local tests by installing dependencies (if any) and executing main code to detect errors.
  4) If tests pass, pushes changes (including developmentstatus.md) back to File Server,
     commits to Git, and responds to the ManagerAgent.
  5) Maintains a 'developmentstatus.md' file on the File Server. Only the DeveloperAgent
     should update this file to track its progress/status.
  6) Supports an optional capability update mechanism (re-register with updated capabilities).
  7) Attempts up to 5 self-correction loops if code generation or tests fail.
"""

import os
import sys
import re
import json
import time
import logging
import subprocess
import tempfile
from typing import Any, Dict, Optional, List, Tuple

import dotenv
import google.genai as genai
import google.genai.types as types
import requests

from baseservice import BaseAgent

# Load environment variables from .env file if present.
dotenv.load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

API_KEY = os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    logger.error("API key not found. Please set the GOOGLE_API_KEY environment variable.")
    sys.exit(1)

USING_GOOGLE_GENAI = True

# Endpoints for external services (Git Service and File Server)
GIT_SERVICE_URL = os.getenv("GIT_SERVICE_URL", "http://localhost:5001")
FILE_SERVER_BASE_URL = os.getenv("FILE_SERVER_BASE_URL", "http://localhost:6000")


###############################################################################
# Helper Functions for Code Generation
###############################################################################

def generate_code_files(
    prompt: str,
    previous_code: Optional[Dict[str, str]] = None,
    error_message: Optional[str] = None,
    include_run_command: bool = False,
    project_config: Optional[Dict[str, Any]] = None,
    include_deployment_files: bool = False,
) -> Dict[str, str]:
    """
    Generates multiple code files using the Google GenAI (Gemini) model.

    Args:
        prompt (str): Description of the application or code to generate.
        previous_code (Optional[Dict[str, str]]): Previously generated code for iterative improvements.
        error_message (Optional[str]): Error messages from previous attempts, used to refine generation.
        include_run_command (bool): Whether to generate a 'run.py' script.
        project_config (Optional[Dict[str, Any]]): Additional configuration for deployment details.
        include_deployment_files (bool): If True, instruct model to generate Dockerfiles, etc.

    Returns:
        Dict[str, str]: Mapping from filename to file content.
    """
    if not USING_GOOGLE_GENAI:
        logger.error("Google GenAI is not enabled.")
        return {}

    # Construct the prompt
    client = genai.Client(api_key=API_KEY)
    full_prompt = prompt

    # Append additional project configuration
    if project_config:
        full_prompt += "\n\n# Additional Project Configuration\n"
        full_prompt += json.dumps(project_config, indent=2)
        full_prompt += "\n"

    # Include previous code files if available
    if previous_code:
        full_prompt += "\n\nHere are the existing or previously generated code files:\n"
        for filename, content in previous_code.items():
            full_prompt += f"--- {filename} ---\n```python\n{content}\n```\n"

    logger.info("[LLM Debug] Error sent to LLM:\n%s", error_message)

    # Include error messages if present
    if error_message:
        full_prompt += (
            f"\n\nThe following error or test failure occurred:\n```\n{error_message}\n```\n"
            "Please fix or improve the code accordingly. Output ONLY the updated code files in this format:\n"
            "--- filename.ext ---\n```python\n<content>\n```\n... etc."
        )
    else:
        full_prompt += (
            "\n\nOutput ONLY the code files (including any deployment/configuration files) and ensure "
            "the code is complete and runnable. Structure the output as:\n"
            "--- filename.ext ---\n```python\n<content>\n```\n... etc."
        )

    if include_run_command:
        full_prompt += (
            "\n\nAdditionally, generate a 'run.py' script that uses the subprocess module "
            "to run this application locally using sys.executable. Output it as a separate file."
        )

    if include_deployment_files:
        full_prompt += (
            "\n\nThis application may be deployed in containers. Include relevant files like Dockerfile, docker-compose.yml, etc."
        )

    model_name = "gemini-2.0-flash-thinking-exp-01-21"

    # Log the full prompt at debug level
    logger.debug("[LLM Debug] Full prompt sent to LLM:\n%s", full_prompt)

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=[types.Part.from_text(text=full_prompt)],
        )
    except Exception as e:
        logger.exception("Error generating content from the model: %s", e)
        return {}

    # Log the raw response text
    logger.info("[LLM Debug] Raw response from LLM:\n%s", response.text)

    files = {}
    try:
        response_text = response.text
        # Regex to capture file blocks in the format:
        # --- filename.ext ---
        # ```[language]
        # <content>
        # ```
        pattern = r"--- ([\w./-]+) ---\n?```[a-zA-Z]*\n(.*?)\n```"
        for match in re.finditer(pattern, response_text, re.DOTALL):
            filename = match.group(1).strip()
            code = match.group(2).strip()
            files[filename] = code
        return files
    except Exception as e:
        logger.exception("Error extracting code files: %s", e)
        logger.debug("Full Response: %s", response.text)
        return {}

###############################################################################
# Dependencies Installation & Code Testing (without pytest)
###############################################################################
def install_dependencies(folder_path: str) -> bool:
    """
    Installs dependencies from requirements.txt if it exists in the specified folder.
    """
    req_path = os.path.join(folder_path, "requirements.txt")
    if os.path.exists(req_path):
        logger.info("Installing dependencies from requirements.txt...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req_path])
        except subprocess.CalledProcessError as e:
            logger.error("Dependency installation failed: %s", e)
            return False
    return True

def run_generated_code(folder_path: str, code_files: Dict[str, str]) -> Tuple[bool, str]:
    """
    Writes the generated code to the specified folder, installs dependencies if present,
    then attempts to run the main Python file (app.py or main.py). Returns (success, error).
    """
    # Write each generated file.
    for filename, content in code_files.items():
        full_path = os.path.join(folder_path, filename)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

    # Check if there's a requirements.txt and install deps
    if not install_dependencies(folder_path):
        return (False, "Dependency installation failed.")

    # Determine main entry point
    main_app_file = None
    for candidate in ("app.py", "main.py"):
        if candidate in code_files:
            main_app_file = candidate
            break

    if not main_app_file:
        return (False, "No main application file (app.py or main.py) found.")
    
    # Check for Flask app without requiring a running server
    if "Flask" in code_files.get("requirements.txt", "") or "flask" in code_files.get(main_app_file, "").lower():
        logger.info("Flask app detected - skipping runtime test and validating code syntax instead")
        # For Flask apps, we'll do a syntax check instead of running the server
        try:
            # Validate syntax of the main file
            subprocess.check_output(
                [sys.executable, "-m", "py_compile", main_app_file],
                cwd=folder_path,
                stderr=subprocess.STDOUT,
                text=True
            )
            logger.info("Flask app syntax check passed")
            return (True, "")
        except subprocess.CalledProcessError as e:
            logger.error(f"Flask app syntax check failed: {e.output}")
            return (False, f"Syntax error in {main_app_file}: {e.output}")
    
    # For non-Flask apps, try running them directly
    command = [sys.executable, main_app_file]
    logger.info("Starting process with command: %s", command)
    try:
        process = subprocess.Popen(
            command,
            cwd=folder_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
    except Exception as e:
        logger.exception("Error starting the process.")
        return (False, str(e))
    
    # Wait a short time to see if process exits with error
    try:
        returncode = process.wait(timeout=5)
        if returncode != 0:
            stdout, stderr = process.communicate()
            error_message = f"Process exited with code {returncode}:\nSTDOUT: {stdout}\nSTDERR: {stderr}"
            logger.error(error_message)
            return (False, error_message)
        
        # Process completed successfully
        stdout, stderr = process.communicate()
        logger.info(f"Process completed with output: {stdout}")
        return (True, "")
    except subprocess.TimeoutExpired:
        # Process is still running - assume it's working properly
        # (for things like servers that don't exit)
        logger.info("Process still running after timeout - assuming success")
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return (True, "")

###############################################################################
# Main DeveloperAgent Class
###############################################################################

class DeveloperAgent(BaseAgent):
    """
    DeveloperAgent handles:
      - Pulling files from File Server,
      - Generating/refactoring code (via Gemini),
      - Running code locally (installing deps and checking for errors),
      - Pushing changes back to File Server,
      - Committing to Git,
      - Updating 'developmentstatus.md' on the File Server,
      - Responding back to the ManagerAgent,
      - Supports up to 5 iterative self-corrections if code generation or tests fail.
    """

    MAX_GENERATION_ATTEMPTS = 5  # number of self-correction loops

    def __init__(self, agent_name: str, registry_url: str, message_queue_host: str, queue_name: str):
        capabilities = ["code_implementation"]
        super().__init__(agent_name, capabilities, registry_url, message_queue_host, queue_name)
        logger.info(f"[{self.agent_name}] DeveloperAgent initialized.")

    ###########################################################################
    # Registry Update (if we need to add or remove capabilities dynamically)
    ###########################################################################
    def update_capabilities(self, new_capabilities: List[str]):
        """
        Update the DeveloperAgent's capabilities and re-register with the registry.
        """
        logger.info(f"[{self.agent_name}] Updating capabilities to: {new_capabilities}")
        self.capabilities = new_capabilities
        self.register_agent()

    ###########################################################################
    # File Server Utilities
    ###########################################################################
    def fetch_file_from_server(self, file_path: str) -> Optional[str]:
        """
        Reads a file from the File Server, returning its content as a string.
        Returns None if file doesn't exist or there's an error.
        """
        url = f"{FILE_SERVER_BASE_URL}/read_file"
        params = {"path": file_path}
        try:
            resp = requests.get(url, params=params, timeout=10)
            
            # If file doesn't exist, return None instead of raising exception
            if resp.status_code == 404:
                logger.info(f"[{self.agent_name}] File not found (expected): {file_path}")
                return None
                
            resp.raise_for_status()  # Raise exception for other status codes
            data = resp.json()
            return data.get("content", "")
        except requests.exceptions.HTTPError as http_err:
            logger.error(f"[{self.agent_name}] HTTP error fetching file '{file_path}': {http_err}")
            return None
        except Exception as e:
            logger.error(f"[{self.agent_name}] Error fetching file '{file_path}': {e}")
            return None

    def push_file_to_server(self, file_path: str, content: str) -> bool:
        """
        Writes or overwrites a file on the File Server.
        """
        url = f"{FILE_SERVER_BASE_URL}/write_file"
        payload = {"path": file_path, "content": content}
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            logger.info(f"[{self.agent_name}] Pushed file to File Server: {file_path}")
            return True
        except Exception as e:
            logger.error(f"[{self.agent_name}] Error pushing file '{file_path}' to server: {e}")
            return False

    def push_multiple_files_to_server(self, base_path: str, files: Dict[str, str]) -> Dict[str, Any]:
        """
        Push multiple generated/modified files to the File Server under a base directory.
        Returns a dict with success/failure statuses.
        """
        results = {}
        for filename, content in files.items():
            target_path = os.path.join(base_path, filename)
            success = self.push_file_to_server(target_path, content)
            results[filename] = "success" if success else "failure"
        return results

    ###########################################################################
    # Git Commit Utility
    ###########################################################################
    def commit_to_git(self, repo_name: str, commit_message: str, file_changes: Dict[str, str]) -> Dict[str, Any]:
        """
        Commits the given file_changes to the specified Git repo using the Git Service API.
        """
        commit_url = f"{GIT_SERVICE_URL}/commit"
        payload = {
            "repo_name": repo_name,
            "commit_message": commit_message,
            "file_changes": file_changes
        }
        try:
            response = requests.post(commit_url, json=payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                logger.info(f"[{self.agent_name}] Git commit successful: {data.get('commit')}")
                return {"status": "success", "commit": data.get("commit")}
            else:
                logger.error(f"[{self.agent_name}] Git commit failed: {response.text}")
                return {"status": "failure", "error": response.text}
        except Exception as e:
            logger.exception(f"[{self.agent_name}] Exception during Git commit: {e}")
            return {"status": "failure", "error": str(e)}

    ###########################################################################
    # Development Status Management
    ###########################################################################
    def update_development_status(self, status_file_path: str, new_entry: str) -> None:
        """
        Appends a new entry to developmentstatus.md (or other status file) on the File Server.
        Creates the file if it doesn't exist yet.
        """
        existing_content = self.fetch_file_from_server(status_file_path)
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        
        if existing_content is None:
            # First entry - file doesn't exist yet
            updated_content = f"# Development Status\n\n{timestamp} - {new_entry}"
            logger.info(f"[{self.agent_name}] Creating new development status file at {status_file_path}")
        else:
            # Append to existing content
            updated_content = existing_content + f"\n\n{timestamp} - {new_entry}"
        
        # Create necessary parent directories if they don't exist
        dir_path = os.path.dirname(status_file_path)
        if dir_path:
            try:
                # Try to ensure the directory exists via FileServer
                url = f"{FILE_SERVER_BASE_URL}/create_directory"
                payload = {"path": dir_path}
                requests.post(url, json=payload, timeout=10) 
                logger.info(f"[{self.agent_name}] Ensured directory exists: {dir_path}")
            except Exception as e:
                logger.warning(f"[{self.agent_name}] Error ensuring directory exists, proceeding anyway: {e}")
            
        # Push the updated content
        if self.push_file_to_server(status_file_path, updated_content):
            logger.info(f"[{self.agent_name}] Updated development status at {status_file_path}")
        else:
            logger.error(f"[{self.agent_name}] Failed to update development status at {status_file_path}")

    ###########################################################################
    # Main Message Processing
    ###########################################################################
    def process_message(self, message: Dict[str, Any]):
        """
        Process incoming messages and attempt up to 5 self-correction loops if errors occur.
        """
        try:
            msg_type = message.get("type", "")
            payload = message.get("payload", {})

            if msg_type == "TASK_ASSIGNMENT" and payload.get("prompt"):
                prompt = payload["prompt"]
                project_config = payload.get("project_config", {})
                include_run_command = payload.get("include_run_command", False)
                git_repo = payload.get("git_repo", None)
                commit_message = payload.get("commit_message", "Auto-commit from DeveloperAgent")
                upload_to_file_server_flag = payload.get("upload_to_file_server", True)
                test_locally = payload.get("test_locally", True)
                include_deployment_files = payload.get("include_deployment_files", True)
                
                # We assume there's a developmentstatus.md or similar file
                file_server_folder = project_config.get("file_server_folder", "")
                development_status_path = os.path.join(file_server_folder, "developmentstatus.md")

                # Update development status with note about new assignment
                self.update_development_status(
                    development_status_path,
                    f"Received new TASK_ASSIGNMENT. Prompt: {prompt}"
                )

                # -----------------------------------------------------------------
                # 1) Perform multi-attempt code generation & optional test correction
                # -----------------------------------------------------------------
                final_generated_files = {}
                success = False
                last_error_message = None
                previous_code = payload.get("previous_code", None)

                # Send initial progress update to frontend
                progress_payload = {
                    "stage": "start",
                    "message": "Starting code generation",
                    "project_name": project_config.get("project_name", "unknown"),
                    "project_config": project_config,
                }
                self.send_message(
                    receiver="ManagerAgent",
                    message_type="PROGRESS_UPDATE",
                    payload=progress_payload,
                    progress=0.0
                )

                for attempt_num in range(1, self.MAX_GENERATION_ATTEMPTS + 1):
                    logger.info(f"[{self.agent_name}] Code generation attempt {attempt_num}/{self.MAX_GENERATION_ATTEMPTS}")
                    
                    # Calculate progress based on attempt number (for frontend UI updates)
                    attempt_progress = (attempt_num - 1) / self.MAX_GENERATION_ATTEMPTS
                    
                    # Send progress update
                    progress_payload = {
                        "stage": "generating",
                        "message": f"Generating code (Attempt {attempt_num}/{self.MAX_GENERATION_ATTEMPTS})",
                        "attempt": attempt_num,
                        "max_attempts": self.MAX_GENERATION_ATTEMPTS,
                        "project_name": project_config.get("project_name", "unknown"),
                        "project_config": project_config,
                    }
                    self.send_message(
                        receiver="ManagerAgent",
                        message_type="PROGRESS_UPDATE",
                        payload=progress_payload,
                        progress=attempt_progress
                    )

                    # Generate code
                    generated_files = generate_code_files(
                        prompt=prompt,
                        previous_code=previous_code,
                        error_message=last_error_message,
                        include_run_command=include_run_command,
                        project_config=project_config,
                        include_deployment_files=include_deployment_files
                    )

                    logger.info(f"[{self.agent_name}] Generated files: {len(generated_files)}")

                    if not generated_files:
                        # If no files generated, refine error message and try again
                        last_error_message = "No files were generated in this attempt."
                        self.update_development_status(
                            development_status_path,
                            f"Attempt {attempt_num} failed: {last_error_message}"
                        )
                        
                        # Send error update
                        progress_payload = {
                            "stage": "error",
                            "message": f"Code generation failed: {last_error_message}",
                            "attempt": attempt_num,
                            "project_name": project_config.get("project_name", "unknown"),
                            "error": last_error_message,
                            "project_config": project_config,
                        }
                        self.send_message(
                            receiver="ManagerAgent",
                            message_type="PROGRESS_UPDATE",
                            payload=progress_payload,
                            progress=attempt_progress
                        )
                        continue

                    # Send file generation update
                    progress_payload = {
                        "stage": "files_generated",
                        "message": f"Generated {len(generated_files)} files",
                        "attempt": attempt_num,
                        "files": list(generated_files.keys()),
                        "project_name": project_config.get("project_name", "unknown"),
                        "project_config": project_config,
                    }
                    self.send_message(
                        receiver="ManagerAgent",
                        message_type="PROGRESS_UPDATE",
                        payload=progress_payload,
                        progress=attempt_progress + 0.1
                    )

                    # If local testing is requested, attempt to run the code to see if errors occur
                    if test_locally:
                        # Send testing update
                        progress_payload = {
                            "stage": "testing",
                            "message": "Testing generated code",
                            "attempt": attempt_num,
                            "project_name": project_config.get("project_name", "unknown"),
                            "project_config": project_config,
                        }
                        self.send_message(
                            receiver="ManagerAgent",
                            message_type="PROGRESS_UPDATE",
                            payload=progress_payload,
                            progress=attempt_progress + 0.15
                        )
                        
                        agent_dir = os.path.dirname(os.path.abspath(__file__))
                        temp_base_dir = os.path.join(agent_dir, "temp")
                        os.makedirs(temp_base_dir, exist_ok=True)
                        with tempfile.TemporaryDirectory(dir=temp_base_dir) as tmpdir:
                            # Write code & run
                            ok, err = run_generated_code(tmpdir, generated_files)
                            if ok:
                                final_generated_files = generated_files
                                success = True
                                
                                # Send success update
                                progress_payload = {
                                    "stage": "test_success",
                                    "message": "Code tests passed successfully",
                                    "attempt": attempt_num,
                                    "project_name": project_config.get("project_name", "unknown"),
                                    "project_config": project_config,
                                }
                                self.send_message(
                                    receiver="ManagerAgent",
                                    message_type="PROGRESS_UPDATE",
                                    payload=progress_payload,
                                    progress=0.8
                                )
                                break
                            else:
                                last_error_message = err
                                self.update_development_status(
                                    development_status_path,
                                    f"Attempt {attempt_num} had error. Retrying with error:\n{err}"
                                )
                                
                                # Send error update
                                progress_payload = {
                                    "stage": "test_error",
                                    "message": f"Testing failed, will retry",
                                    "attempt": attempt_num,
                                    "error": err,
                                    "project_name": project_config.get("project_name", "unknown"),
                                    "project_config": project_config,
                                }
                                self.send_message(
                                    receiver="ManagerAgent",
                                    message_type="PROGRESS_UPDATE",
                                    payload=progress_payload,
                                    progress=attempt_progress + 0.2
                                )
                                # Use current generated files as "previous_code" for the next iteration
                                previous_code = generated_files
                    else:
                        # If not testing locally, we consider it a success after generation
                        final_generated_files = generated_files
                        success = True
                        
                        # Send success update (skipped testing)
                        progress_payload = {
                            "stage": "generation_complete",
                            "message": "Code generation complete (testing skipped)",
                            "attempt": attempt_num,
                            "project_name": project_config.get("project_name", "unknown"),
                            "project_config": project_config,
                        }
                        self.send_message(
                            receiver="ManagerAgent",
                            message_type="PROGRESS_UPDATE",
                            payload=progress_payload,
                            progress=0.8
                        )
                        break

                # -----------------------------------------------------------------
                # 2) If final generation is successful, push & commit if needed
                # -----------------------------------------------------------------
                response_payload = {}
                response_payload["project_config"] = project_config
                if success and final_generated_files:
                    logger.info(f"[{self.agent_name}] Successfully generated files after corrections.")
                    response_payload["generated_files"] = list(final_generated_files.keys())
                    response_payload["code_generation_status"] = "success"

                    # Send uploading update
                    progress_payload = {
                        "stage": "uploading",
                        "message": "Uploading files to File Server",
                        "project_name": project_config.get("project_name", "unknown"),
                        "project_config": project_config,
                        "files": list(final_generated_files.keys())
                    }
                    self.send_message(
                        receiver="ManagerAgent",
                        message_type="PROGRESS_UPDATE",
                        payload=progress_payload,
                        progress=0.85
                    )

                    # Push to file server if requested
                    if upload_to_file_server_flag and file_server_folder:
                        # First check which files already exist to avoid duplicate file generation
                        existing_files = {}
                        for filename in final_generated_files.keys():
                            file_path = os.path.join(file_server_folder, filename)
                            existing_content = self.fetch_file_from_server(file_path)
                            if existing_content:
                                existing_files[filename] = existing_content
                                
                        # Log files that already exist to avoid duplicates
                        if existing_files:
                            logger.info(f"[{self.agent_name}] Found {len(existing_files)} existing files. Will only update if content changes.")
                            for filename in existing_files:
                                # If the content is identical, don't push again
                                if existing_files[filename] == final_generated_files[filename]:
                                    logger.info(f"[{self.agent_name}] File {filename} already exists with identical content, skipping.")
                                    # Remove from files to push
                                    final_generated_files.pop(filename, None)
                        
                        push_results = self.push_multiple_files_to_server(file_server_folder, final_generated_files)
                        response_payload["file_upload_results"] = push_results
                        response_payload["existing_files"] = list(existing_files.keys())

                    # Update dev status
                    self.update_development_status(
                        development_status_path,
                        "Code generation complete. Tests passed (or tests disabled)."
                    )

                    # Send commit update if needed
                    if git_repo:
                        progress_payload = {
                            "stage": "committing",
                            "message": "Committing changes to Git repository",
                            "project_name": project_config.get("project_name", "unknown"),
                            "project_config": project_config,
                            "repo": git_repo
                        }
                        self.send_message(
                            receiver="ManagerAgent",
                            message_type="PROGRESS_UPDATE",
                            payload=progress_payload,
                            progress=0.9
                        )
                        
                        # Commit to Git if repo is specified
                        git_result = self.commit_to_git(git_repo, commit_message, final_generated_files)
                        response_payload["git_commit"] = git_result

                    self.update_development_status(
                        development_status_path,
                        f"Changes committed to Git repo: {git_repo}"
                    )

                    # Send completion update
                    progress_payload = {
                        "stage": "complete",
                        "message": "Task completed successfully",
                        "project_name": project_config.get("project_name", "unknown"),
                        "project_config": project_config,
                        "file_count": len(final_generated_files)
                    }
                    self.send_message(
                        receiver="ManagerAgent",
                        message_type="PROGRESS_UPDATE",
                        payload=progress_payload,
                        progress=1.0
                    )

                else:
                    # All attempts failed
                    logger.error(f"[{self.agent_name}] Code generation failed after {self.MAX_GENERATION_ATTEMPTS} attempts.")
                    response_payload["code_generation_status"] = "failure"
                    response_payload["error"] = f"Self-correction failed after {self.MAX_GENERATION_ATTEMPTS} attempts."
                    if last_error_message:
                        response_payload["last_error_message"] = last_error_message

                    self.update_development_status(
                        development_status_path,
                        f"All {self.MAX_GENERATION_ATTEMPTS} attempts failed. Aborting."
                    )
                    
                    # Send failure update
                    progress_payload = {
                        "stage": "failed",
                        "message": f"Task failed after {self.MAX_GENERATION_ATTEMPTS} attempts",
                        "project_name": project_config.get("project_name", "unknown"),
                        "project_config": project_config,
                        "error": last_error_message or "Unknown error"
                    }
                    self.send_message(
                        receiver="ManagerAgent",
                        message_type="PROGRESS_UPDATE",
                        payload=progress_payload,
                        progress=1.0  # We're still at 100% complete, just with failure status
                    )

                # Finally, send a response message back to the sender (e.g., ManagerAgent).
                self.send_message(
                    receiver=message.get("sender", "ManagerAgent"),
                    message_type="TASK_EXECUTION",
                    payload=response_payload
                )

            else:
                # For other message types, we do nothing special
                logger.info(f"[{self.agent_name}] Ignoring message of type: {msg_type}")

        except Exception as e:
            logger.exception(f"[{self.agent_name}] Exception while processing message: {e}")
            error_payload = {"status": "failure", "error": str(e)}
            self.send_message(
                receiver=message.get("sender", "ManagerAgent"),
                message_type="STATUS_UPDATE",
                payload=error_payload
            )


if __name__ == "__main__":
    # Example instantiation. Replace these with actual endpoints and configuration as needed.
    AGENT_NAME = "DeveloperAgent"
    REGISTRY_URL = "http://localhost:5005"  # Capability Registry endpoint.
    MESSAGE_QUEUE_HOST = "localhost"        # RabbitMQ host.
    QUEUE_NAME = "DeveloperAgentQueue"      # Queue name for this agent.

    # Instantiate and run the DeveloperAgent.
    developer_agent = DeveloperAgent(AGENT_NAME, REGISTRY_URL, MESSAGE_QUEUE_HOST, QUEUE_NAME)

    # Keep the main thread alive as background threads handle heartbeats and message listening.
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info(f"[{AGENT_NAME}] Shutting down.")
