#!/usr/bin/env python
"""
TestingAgent.py

A Testing Agent responsible for:
  1) Receiving and handling TEST_REQUEST and TEST_GENERATION_REQUEST messages.
  2) Pulling code/test files from the FileServer.
  3) (Optional) Generating new test files from the existing codebase using an LLM.
  4) Installing dependencies (if needed).
  5) Running tests (via pytest or a custom command).
  6) Uploading test results back to the FileServer (optional).
  7) Sending final results to the ManagerAgent in a TASK_EXECUTION message to continue the flow.
"""

import os
import sys
import time
import logging
import json
import subprocess
import tempfile
from typing import Dict, Any, Optional, Tuple

import requests
from baseservice import BaseAgent

# If you're using Google GenAI, install and import the client library.
# For demonstration, we'll show stubs that mirror DeveloperAgent's usage.
import re
try:
    import google.genai as genai
    import google.genai.types as types
    USING_GOOGLE_GENAI = True
except ImportError:
    USING_GOOGLE_GENAI = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Environment variables / default endpoints
FILE_SERVER_BASE_URL = os.getenv("FILE_SERVER_BASE_URL", "http://localhost:6000")
GIT_SERVICE_URL = os.getenv("GIT_SERVICE_URL", "http://localhost:5001")
API_KEY = os.getenv("GOOGLE_API_KEY")  # needed for LLM-based test generation


class TestingAgent(BaseAgent):
    """
    The TestingAgent handles:
      - TEST_REQUEST messages to run tests on existing code.
      - TEST_GENERATION_REQUEST messages to generate new tests from existing code.
      - Publishes progress updates (PROGRESS_UPDATE) and final outcomes (TASK_EXECUTION) 
        to the ManagerAgent so the manager can continue the flow.
    """

    def __init__(self, agent_name: str, registry_url: str, message_queue_host: str, queue_name: str):
        # Example capabilities: "automated_testing", "test_reports", "test_generation"
        capabilities = ["automated_testing", "test_reports", "test_generation"]
        super().__init__(agent_name, capabilities, registry_url, message_queue_host, queue_name)
        logger.info(f"[{self.agent_name}] TestingAgent initialized.")

    def process_message(self, message: Dict[str, Any]):
        """
        Main entry point for handling incoming messages.
        """
        try:
            msg_type = message.get("type", "")
            payload = message.get("payload", {})

            if msg_type == "TEST_REQUEST":
                logger.info(f"[{self.agent_name}] Received TEST_REQUEST: {payload}")
                self.handle_test_request(message)

            elif msg_type == "TEST_GENERATION_REQUEST":
                logger.info(f"[{self.agent_name}] Received TEST_GENERATION_REQUEST: {payload}")
                self.handle_test_generation_request(message)
                
            elif msg_type == "TASK_ASSIGNMENT":
                # A generic "TASK_ASSIGNMENT" possibly requesting test generation or test run
                logger.info(f"[{self.agent_name}] Received TASK_ASSIGNMENT: {payload}")
                self.handle_task_assignment(message)

            else:
                logger.info(f"[{self.agent_name}] Ignoring message of type: {msg_type}")

        except Exception as e:
            logger.exception(f"[{self.agent_name}] Exception while processing message: {e}")
            error_payload = {"status": "failure", "error": str(e)}
            # Send an error status update back to the ManagerAgent
            self.send_message(
                receiver="ManagerAgent",
                message_type="STATUS_UPDATE",
                payload=error_payload
            )

    def handle_task_assignment(self, message: Dict[str, Any]):
        """
        Convert a TASK_ASSIGNMENT to either a TEST_REQUEST or TEST_GENERATION_REQUEST,
        based on the capabilities or 'reason' provided.
        """
        payload = message.get("payload", {})
        reason_text = payload.get("reason", "").lower()
        if "test_generation" in reason_text or "generate test" in reason_text:
            message["type"] = "TEST_GENERATION_REQUEST"
            self.handle_test_generation_request(message)
        elif "automated_testing" in reason_text or "run test" in reason_text:
            message["type"] = "TEST_REQUEST"
            self.handle_test_request(message)
        else:
            # Fallback: just do a test run
            message["type"] = "TEST_REQUEST"
            self.handle_test_request(message)

    def handle_test_request(self, message: Dict[str, Any]):
        """
        Respond to a TEST_REQUEST:
          1) Fetch code from the File Server
          2) Install dependencies
          3) Run tests (via pytest, by default)
          4) Push results to File Server (optional)
          5) Send final outcome back to ManagerAgent as a TASK_EXECUTION message
        """
        payload = message.get("payload", {})
        project_config = payload.get("project_config", {})
        file_server_folder = project_config.get("file_server_folder", "")
        git_repo = project_config.get("repo_name", None)
        commit_message = payload.get("commit_message", "Auto-commit test results")
        run_pytest_flag = payload.get("run_pytest", True)
        test_results_file = payload.get("test_results_file", "test_results.md")
        test_folder = payload.get("test_folder", "")

        # --- Send initial progress update to ManagerAgent ---
        self.send_message(
            receiver="ManagerAgent",
            message_type="PROGRESS_UPDATE",
            payload={
                "stage": "start_test",
                "message": f"Starting test run for project: {project_config.get('project_name', '')}",
                "project_name": project_config.get("project_name", "")
            },
            progress=0.0
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            # Fetch the entire folder from File Server
            self.fetch_entire_folder(file_server_folder, tmpdir)

            # Send progress update
            self.send_message(
                receiver="ManagerAgent",
                message_type="PROGRESS_UPDATE",
                payload={
                    "stage": "install_deps",
                    "message": "Installing dependencies if present",
                    "project_name": project_config.get("project_name", "")
                },
                progress=0.2
            )

            # Install dependencies
            self.install_dependencies(tmpdir)

            # If run_pytest is requested, do it
            stdout, stderr = "", ""
            success = False
            if run_pytest_flag:
                self.send_message(
                    receiver="ManagerAgent",
                    message_type="PROGRESS_UPDATE",
                    payload={
                        "stage": "running_tests",
                        "message": "Running pytest",
                        "project_name": project_config.get("project_name", "")
                    },
                    progress=0.4
                )
                success, stdout, stderr = self.run_pytest(tmpdir, test_folder=test_folder)

            test_results = {
                "success": success,
                "stdout": stdout,
                "stderr": stderr
            }

            # Optionally push test results to File Server
            if test_results_file:
                content_str = (
                    f"# Test Results\n\n**Success:** {success}\n\n"
                    f"## STDOUT\n```\n{stdout}\n```\n\n"
                    f"## STDERR\n```\n{stderr}\n```\n"
                )
                target_path = os.path.join(file_server_folder, test_results_file)
                self.push_file_to_server(target_path, content_str)

                # Optionally commit to Git
                if git_repo:
                    file_changes = {test_results_file: content_str}
                    commit_resp = self.commit_to_git(
                        repo_name=git_repo,
                        commit_message=commit_message,
                        file_changes=file_changes
                    )
                    test_results["git_commit"] = commit_resp

        # Send final outcome as TASK_EXECUTION
        final_payload = {
            "test_execution_status": "success" if success else "failure",
            "test_results": test_results,
            "project_config": project_config
        }

        self.send_message(
            receiver="ManagerAgent",
            message_type="TASK_EXECUTION",
            payload=final_payload
        )

        self.send_message(
            receiver="ManagerAgent",
            message_type="PROGRESS_UPDATE",
            payload={
                "stage": "complete",
                "message": f"Testing complete for project: {project_config.get('project_name','')}",
                "project_name": project_config.get("project_name", "")
            },
            progress=1.0
        )


    def handle_test_generation_request(self, message: Dict[str, Any]):
        """
        Respond to a TEST_GENERATION_REQUEST:
          1) Fetch code from File Server
          2) Generate test files (via LLM if possible, or fallback)
          3) Push test files to File Server
          4) (Optional) run tests
          5) Send final outcome back to ManagerAgent as a TASK_EXECUTION message
        """
        payload = message.get("payload", {})
        project_config = payload.get("project_config", {})
        file_server_folder = project_config.get("file_server_folder", "")
        git_repo = project_config.get("repo_name", None)
        commit_message = payload.get("commit_message", "Auto-commit test files")
        run_pytest_flag = payload.get("run_pytest", True)
        test_results_file = payload.get("test_results_file", "test_results.md")
        test_folder = payload.get("test_folder", "tests")  # default tests folder

        # --- Send initial progress update to ManagerAgent ---
        self.send_message(
            receiver="ManagerAgent",
            message_type="PROGRESS_UPDATE",
            payload={
                "stage": "start_test_generation",
                "message": f"Starting test generation for project: {project_config.get('project_name', '')}",
                "project_name": project_config.get("project_name", "")
            },
            progress=0.0
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            # Fetch existing code
            self.send_message(
                receiver="ManagerAgent",
                message_type="PROGRESS_UPDATE",
                payload={
                    "stage": "fetch_code",
                    "message": "Fetching code from File Server",
                    "project_name": project_config.get("project_name", "")
                },
                progress=0.2
            )
            self.fetch_entire_folder(file_server_folder, tmpdir)

            # Generate test files
            self.send_message(
                receiver="ManagerAgent",
                message_type="PROGRESS_UPDATE",
                payload={
                    "stage": "generate_tests",
                    "message": "Generating tests via LLM",
                    "project_name": project_config.get("project_name", "")
                },
                progress=0.3
            )
            generated_files = self.generate_test_files(tmpdir)
            logger.info(f"[{self.agent_name}] LLM generated {len(generated_files)} file(s).")

            # Push generated tests to File Server
            push_results = {}
            for filename, content in generated_files.items():
                target_path = os.path.join(file_server_folder, test_folder, filename)
                ok = self.push_file_to_server(target_path, content)
                push_results[filename] = "success" if ok else "failure"

            # (Optional) run tests immediately
            test_run_data = {"success": None, "stdout": "", "stderr": ""}
            if run_pytest_flag:
                # Install dependencies
                self.send_message(
                    receiver="ManagerAgent",
                    message_type="PROGRESS_UPDATE",
                    payload={
                        "stage": "install_deps",
                        "message": "Installing dependencies for test run",
                        "project_name": project_config.get("project_name", "")
                    },
                    progress=0.5
                )
                self.install_dependencies(tmpdir)

                # Write the newly generated tests into our temp dir so we can run them
                for filename, content in generated_files.items():
                    tests_subdir = os.path.join(tmpdir, test_folder)
                    os.makedirs(tests_subdir, exist_ok=True)
                    with open(os.path.join(tests_subdir, filename), "w", encoding="utf-8") as f:
                        f.write(content)

                self.send_message(
                    receiver="ManagerAgent",
                    message_type="PROGRESS_UPDATE",
                    payload={
                        "stage": "run_pytest",
                        "message": "Running pytest on newly generated tests",
                        "project_name": project_config.get("project_name", "")
                    },
                    progress=0.7
                )
                success, stdout, stderr = self.run_pytest(tmpdir, test_folder=test_folder)
                test_run_data = {
                    "success": success,
                    "stdout": stdout,
                    "stderr": stderr
                }

                # Optionally push test_results.md
                if test_results_file:
                    content_str = (
                        f"# Test Results (LLM-Generated)\n\n**Success:** {success}\n\n"
                        f"## STDOUT\n```\n{stdout}\n```\n\n"
                        f"## STDERR\n```\n{stderr}\n```\n"
                    )
                    results_path = os.path.join(file_server_folder, test_results_file)
                    self.push_file_to_server(results_path, content_str)

                    # Optionally commit to Git
                    if git_repo:
                        file_changes = {test_results_file: content_str}
                        commit_resp = self.commit_to_git(
                            repo_name=git_repo,
                            commit_message=commit_message,
                            file_changes=file_changes
                        )
                        test_run_data["git_commit"] = commit_resp

        # Send final outcome as TASK_EXECUTION
        final_payload = {
            "test_generation_status": "completed",
            "generated_test_files": list(generated_files.keys()),
            "push_results": push_results,
            "test_run_data": test_run_data,
            "project_config": project_config
        }

        self.send_message(
            receiver="ManagerAgent",
            message_type="TASK_EXECUTION",
            payload=final_payload
        )

        self.send_message(
            receiver="ManagerAgent",
            message_type="PROGRESS_UPDATE",
            payload={
                "stage": "complete",
                "message": f"Test generation complete for project: {project_config.get('project_name','')}",
                "project_name": project_config.get("project_name", "")
            },
            progress=1.0
        )


    def generate_test_files(self, local_project_path: str) -> Dict[str, str]:
        """
        Calls the LLM to generate or update test files based on the existing code.
        Returns {filename: content}.
        If Google GenAI isn't installed/available, we create a basic fallback test.
        """
        if not USING_GOOGLE_GENAI or not API_KEY:
            logger.warning(f"[{self.agent_name}] Google GenAI not available, using fallback test generation.")
            return self.fallback_test_generation(local_project_path)

        client = genai.Client(api_key=API_KEY)
        # Gather .py files up to some limit, ignoring test_* files.
        code_snippets = ""
        for root, dirs, files in os.walk(local_project_path):
            for file in files:
                if file.endswith(".py") and not file.lower().startswith("test_"):
                    fullpath = os.path.join(root, file)
                    try:
                        with open(fullpath, "r", encoding="utf-8") as f:
                            code = f.read()
                            code_snippets += f"\n\n# File: {file}\n\n" + code
                    except Exception as e:
                        logger.error(f"Error reading {fullpath}: {e}")
                    if len(code_snippets) > 100000:  # limit
                        break
                if len(code_snippets) > 100000:
                    break
            if len(code_snippets) > 100000:
                break

        # Construct LLM prompt
        prompt = (
            "You are an AI specialized in Python test generation. "
            "Below is Python code from a project. Generate or update unit tests using Pytest. "
            "Output ONLY the test files in the format:\n"
            "--- filename.py ---\n```python\n<content>\n```\n\n"
            f"Here is the code:\n{code_snippets}\n"
        )
        try:
            response = client.models.generate_content(
                model="gemini-2.0-flash-thinking-exp-01-21",
                contents=[types.Part.from_text(text=prompt)],
            )
        except Exception as e:
            logger.exception(f"[{self.agent_name}] Error calling LLM for test generation: {e}")
            return self.fallback_test_generation(local_project_path)

        raw_response = response.text
        logger.debug(f"[LLM Test Generation Response]\n{raw_response}")

        # Regex to parse blocks of the form:
        # --- filename.py ---
        # ```python
        # <content>
        # ```
        pattern = r"--- ([\w./-]+) ---\n```[a-zA-Z]*\n(.*?)\n```"
        test_files = {}
        try:
            for match in re.finditer(pattern, raw_response, re.DOTALL):
                filename = match.group(1).strip()
                code = match.group(2).strip()
                # Ensure filename starts with test_
                if not filename.lower().startswith("test_"):
                    filename = "test_" + filename
                test_files[filename] = code
        except Exception as e:
            logger.exception(f"[{self.agent_name}] Error extracting LLM test files: {e}")
            return self.fallback_test_generation(local_project_path)

        if not test_files:
            # If no files extracted, fall back to a single basic test
            logger.warning("[TestingAgent] LLM returned no test files; using fallback test.")
            return self.fallback_test_generation(local_project_path)

        return test_files

    def fallback_test_generation(self, local_project_path: str) -> Dict[str, str]:
        """
        Fallback if LLM is unavailable or fails to return any test files.
        Creates a single test_basic.py with minimal checks.
        """
        test_content = r"""
import pytest
import os
import sys
import importlib.util
from unittest.mock import patch

def find_app_module():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(current_dir)
    for candidate in ["app.py", "main.py"]:
        app_path = os.path.join(parent_dir, candidate)
        if os.path.exists(app_path):
            spec = importlib.util.spec_from_file_location("app_module", app_path)
            app_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(app_module)
            return app_module
    return None

def test_app_exists():
    app_module = find_app_module()
    assert app_module is not None, "No main entry point (app.py or main.py) found"

def test_example():
    assert True, "A basic test that always passes"
"""
        return {"test_basic.py": test_content}

    # -------------------------------------------------------------------------
    # File Server Utilities
    # -------------------------------------------------------------------------
    def fetch_file_from_server(self, file_path: str) -> Optional[str]:
        """
        Reads a file from the File Server, returning its content as a string.
        """
        url = f"{FILE_SERVER_BASE_URL}/read_file"
        params = {"path": file_path}
        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return data.get("content", "")
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

    def fetch_entire_folder(self, folder_path: str, local_dir: str):
        """
        Recursively fetches all files/subfolders from `folder_path` on the FileServer
        and writes them into `local_dir`.
        """
        list_dir_url = f"{FILE_SERVER_BASE_URL}/list_directory"
        params = {"path": folder_path}
        try:
            resp = requests.get(list_dir_url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("contents", [])
        except Exception as e:
            logger.error(f"[{self.agent_name}] Error listing folder '{folder_path}': {e}")
            return

        for item in items:
            if item.startswith("[DIR]"):
                subdirname = item.replace("[DIR] ", "").strip()
                subfolder_path = os.path.join(folder_path, subdirname)
                local_subdir = os.path.join(local_dir, subdirname)
                os.makedirs(local_subdir, exist_ok=True)
                self.fetch_entire_folder(subfolder_path, local_subdir)
            elif item.startswith("[FILE]"):
                filename = item.replace("[FILE] ", "").strip()
                file_path = os.path.join(folder_path, filename)
                content = self.fetch_file_from_server(file_path)
                if content is not None:
                    local_file = os.path.join(local_dir, filename)
                    os.makedirs(os.path.dirname(local_file), exist_ok=True)
                    with open(local_file, "w", encoding="utf-8") as f:
                        f.write(content)
                else:
                    logger.error(f"[{self.agent_name}] Failed to fetch file: {file_path}")
            else:
                logger.warning(f"[{self.agent_name}] Unknown item type: {item}")

    # -------------------------------------------------------------------------
    # Git Commit Utility
    # -------------------------------------------------------------------------
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

    # -------------------------------------------------------------------------
    # Dependencies & Testing
    # -------------------------------------------------------------------------
    def install_dependencies(self, folder_path: str) -> bool:
        """
        Installs dependencies from requirements.txt if it exists in the specified folder.
        """
        req_path = os.path.join(folder_path, "requirements.txt")
        if os.path.exists(req_path):
            logger.info(f"[{self.agent_name}] Installing dependencies from requirements.txt...")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req_path])
            except subprocess.CalledProcessError as e:
                logger.error(f"[{self.agent_name}] Dependency installation failed: {e}")
                return False
        return True

    def run_pytest(self, folder_path: str, test_folder: str = "") -> Tuple[bool, str, str]:
        """
        Runs pytest in the given folder (or subfolder), capturing stdout/stderr.
        Returns (success, stdout, stderr).
        """
        test_target = os.path.join(folder_path, test_folder) if test_folder else folder_path
        cmd = [sys.executable, "-m", "pytest", "--maxfail=1", "--disable-warnings"]
        logger.info(f"[{self.agent_name}] Running pytest in {test_target} with command: {cmd}")

        try:
            process = subprocess.Popen(
                cmd,
                cwd=test_target,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = process.communicate()
            exit_code = process.returncode

            # Pytest exit_code: 0=all tests passed, 1=tests failed, ...
            success = (exit_code == 0)
            return (success, stdout, stderr)
        except Exception as e:
            logger.exception(f"[{self.agent_name}] Error running pytest: {e}")
            return (False, "", str(e))


if __name__ == "__main__":
    # Example usage
    AGENT_NAME = "TestingAgent"
    REGISTRY_URL = "http://localhost:5005"   # Adjust as needed
    MESSAGE_QUEUE_HOST = "localhost"         # RabbitMQ host
    QUEUE_NAME = "TestingAgentQueue"         # TestingAgent queue

    testing_agent = TestingAgent(AGENT_NAME, REGISTRY_URL, MESSAGE_QUEUE_HOST, QUEUE_NAME)

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info(f"[{AGENT_NAME}] Shutting down.")
