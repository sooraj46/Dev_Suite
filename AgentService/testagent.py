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
  7) Sending TEST_RESULTS or TEST_GENERATION_RESULTS messages back to the requester.
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
      - TEST_REQUEST messages to just run tests.
      - TEST_GENERATION_REQUEST messages to generate new tests from existing code.
    """

    def __init__(self, agent_name: str, registry_url: str, message_queue_host: str, queue_name: str):
        # Example capabilities: "automated_testing", "test_reports", "test_generation"
        capabilities = ["automated_testing", "test_reports", "test_generation"]
        super().__init__(agent_name, capabilities, registry_url, message_queue_host, queue_name)
        logger.info(f"[{self.agent_name}] TestingAgent initialized.")

    def process_message(self, message: Dict[str, Any]):
        """
        Handle incoming messages:
        - type="TEST_REQUEST" for running tests.
        - type="TEST_GENERATION_REQUEST" for auto-generating tests.
        """
        try:
            msg_type = message.get("type", "")
            payload = message.get("payload", {})

            if msg_type == "TEST_REQUEST":
                logger.info(f"[{self.agent_name}] Received TEST_REQUEST with payload: {payload}")
                self.handle_test_request(message)

            elif msg_type == "TEST_GENERATION_REQUEST":
                logger.info(f"[{self.agent_name}] Received TEST_GENERATION_REQUEST with payload: {payload}")
                self.handle_test_generation_request(message)

            else:
                logger.info(f"[{self.agent_name}] Ignoring message of type: {msg_type}")

        except Exception as e:
            logger.exception(f"[{self.agent_name}] Exception while processing message: {e}")
            error_payload = {"status": "failure", "error": str(e)}
            self.send_message(
                receiver=message.get("sender", "UnknownAgent"),
                message_type="STATUS_UPDATE",
                payload=error_payload
            )

    def handle_test_request(self, message: Dict[str, Any]):
        """
        Handle the standard test request: fetch code, install deps, run pytest, return results.
        """
        payload = message.get("payload", {})
        project_config = payload.get("project_config", {})
        file_server_folder = project_config.get("file_server_folder", "")
        git_repo = project_config.get("repo_name", None)
        commit_message = payload.get("commit_message", "Auto-commit test results")
        run_pytest_flag = payload.get("run_pytest", True)
        test_results_file = payload.get("test_results_file", "test_results.md")
        test_folder = payload.get("test_folder", "")

        sender = message.get("sender", "UnknownAgent")

        logger.info(f"[{self.agent_name}] Fetching code from file server folder: {file_server_folder}")
        with tempfile.TemporaryDirectory() as tmpdir:
            self.fetch_entire_folder(file_server_folder, tmpdir)

            # Install dependencies
            self.install_dependencies(tmpdir)

            success, stdout, stderr = (False, "", "No test command provided.")
            if run_pytest_flag:
                success, stdout, stderr = self.run_pytest(tmpdir, test_folder=test_folder)

            test_results = {
                "success": success,
                "stdout": stdout,
                "stderr": stderr,
            }

            # Optionally write test_results to file server
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

        # Finally, respond with TEST_RESULTS
        self.send_message(
            receiver=sender,
            message_type="TEST_RESULTS",
            payload={
                "status": "completed",
                "test_results": test_results,
                "project_config": project_config
            }
        )

    def handle_test_generation_request(self, message: Dict[str, Any]):
        """
        Handle a request to auto-generate unit tests from existing code using an LLM.
        Steps:
         1) Fetch code.
         2) Generate test files with LLM.
         3) Push them to FileServer.
         4) (Optional) run tests.
         5) Return results.
        """
        payload = message.get("payload", {})
        project_config = payload.get("project_config", {})
        file_server_folder = project_config.get("file_server_folder", "")
        git_repo = project_config.get("repo_name", None)
        commit_message = payload.get("commit_message", "Auto-commit test files")
        run_pytest_flag = payload.get("run_pytest", True)
        test_results_file = payload.get("test_results_file", "test_results.md")
        test_folder = payload.get("test_folder", "tests")  # default tests folder

        sender = message.get("sender", "UnknownAgent")

        if not USING_GOOGLE_GENAI or not API_KEY:
            logger.error("[TestingAgent] LLM-based test generation not available (missing Google GenAI or API Key)")
            error_payload = {
                "status": "failure",
                "error": "LLM test generation not available."
            }
            self.send_message(
                receiver=sender,
                message_type="TEST_GENERATION_RESULTS",
                payload=error_payload
            )
            return

        # 1) Fetch the existing code
        with tempfile.TemporaryDirectory() as tmpdir:
            logger.info(f"[{self.agent_name}] Fetching code from file server folder: {file_server_folder} for test generation.")
            self.fetch_entire_folder(file_server_folder, tmpdir)

            # 2) Call the LLM to generate test files
            logger.info(f"[{self.agent_name}] Generating tests via LLM...")
            generated_files = self.generate_test_files(tmpdir)
            logger.info(f"[{self.agent_name}] LLM generated {len(generated_files)} test file(s).")

            # 3) Push them to FileServer under the 'tests' directory by default
            push_results = {}
            for filename, content in generated_files.items():
                # We'll place them in file_server_folder/tests/<filename>
                target_path = os.path.join(file_server_folder, test_folder, filename)
                ok = self.push_file_to_server(target_path, content)
                push_results[filename] = "success" if ok else "failure"

            # 4) (Optional) run tests now
            test_run_data = {"success": None, "stdout": "", "stderr": ""}
            if run_pytest_flag:
                self.install_dependencies(tmpdir)
                # We'll rewrite the newly generated tests into tmpdir so we can run them.
                for filename, content in generated_files.items():
                    test_path = os.path.join(tmpdir, test_folder)
                    os.makedirs(test_path, exist_ok=True)
                    with open(os.path.join(test_path, filename), "w", encoding="utf-8") as f:
                        f.write(content)

                success, stdout, stderr = self.run_pytest(tmpdir, test_folder=test_folder)
                test_run_data = {
                    "success": success,
                    "stdout": stdout,
                    "stderr": stderr
                }

                # Optionally push test_results.md if we have it
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

        # 5) Send TEST_GENERATION_RESULTS
        response_payload = {
            "status": "completed",
            "generated_test_files": list(generated_files.keys()),
            "push_results": push_results,
            "test_run_data": test_run_data,
            "project_config": project_config
        }
        self.send_message(
            receiver=sender,
            message_type="TEST_GENERATION_RESULTS",
            payload=response_payload
        )

    def generate_test_files(self, local_project_path: str) -> Dict[str, str]:
        """
        Calls the LLM to generate or update test files based on the existing code.
        This method will gather the code in `local_project_path`, feed it to the LLM,
        parse out generated files, and return them as {filename: content}.
        """
        if not USING_GOOGLE_GENAI:
            logger.warning(f"[{self.agent_name}] Google GenAI is not enabled.")
            return {}

        client = genai.Client(api_key=API_KEY)
        # We'll gather some of the code in a summary to feed to the LLM.
        # For brevity, let's just read .py files up to some limit.
        code_snippets = ""
        for root, dirs, files in os.walk(local_project_path):
            for file in files:
                if file.endswith(".py") and "test_" not in file.lower():
                    fullpath = os.path.join(root, file)
                    try:
                        with open(fullpath, "r", encoding="utf-8") as f:
                            code = f.read()
                            code_snippets += f"\n\n# File: {file}\n\n" + code
                    except Exception as e:
                        logger.error(f"Error reading {fullpath}: {e}")
                    # Basic safeguard on length
                    if len(code_snippets) > 100000:
                        break
                if len(code_snippets) > 100000:
                    break
            if len(code_snippets) > 100000:
                break

        # Construct a prompt asking the LLM to generate new test files
        prompt = (
            "You are an AI specialized in Python test generation. "
            "Below is Python code from a project. Generate new or updated unit tests using Pytest. "
            "Only output the test files in the format: \n"
            "--- filename.py ---\n"
            "```python\n<content>\n```\n...\n\n"
            "Here is the code:\n" + code_snippets + "\n"
        )

        logger.info(f"[{self.agent_name}] Sending test generation prompt to LLM.")
        try:
            response = client.models.generate_content(
                model="gemini-2.0-flash-thinking-exp-01-21",
                contents=[types.Part.from_text(text=prompt)],
            )
        except Exception as e:
            logger.exception(f"[{self.agent_name}] Error calling LLM for test generation: {e}")
            return {}

        raw_response = response.text
        logger.debug(f"[LLM Test Generation Response]\n{raw_response}")

        # Parse out files from the LLM response.
        # This matches the pattern used in DeveloperAgent.
        pattern = r"--- ([\w./-]+) ---\\n```[a-zA-Z]*\\n(.*?)\\n```"
        test_files = {}
        try:
            for match in re.finditer(pattern, raw_response, re.DOTALL):
                filename = match.group(1).strip()
                code = match.group(2).strip()
                # Ensure the filename starts with test_ if it doesn't already
                if not filename.lower().startswith("test_"):
                    filename = "test_" + filename
                test_files[filename] = code
        except Exception as e:
            logger.exception(f"[{self.agent_name}] Error extracting LLM test files: {e}")
            return {}

        return test_files

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
