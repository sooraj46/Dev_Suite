import os
import json
import time
import requests
import pika
from flask import Flask, request, jsonify, render_template_string

# Configure environment or defaults
MESSAGE_QUEUE_HOST = os.getenv("MESSAGE_QUEUE_HOST", "localhost")
MANAGER_QUEUE = os.getenv("MANAGER_QUEUE", "ManagerAgentQueue")
FILE_SERVER_BASE_URL = os.getenv("FILE_SERVER_BASE_URL", "http://localhost:6000")

app = Flask(__name__)

# Store pending clarification and a log of completed tasks in memory.
# In production, consider using a database or more durable store.
pending_clarification = None
task_executions_log = []  # List of task execution payloads received from ManagerAgent

def publish_to_manager_agent_queue(msg_type: str, payload: dict):
    """
    Publish a JSON message to the RabbitMQ queue that the ManagerAgent is listening to.
    ManagerAgent (manageragent.py) listens on 'MANAGER_QUEUE' by default.
    """
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host=MESSAGE_QUEUE_HOST)
    )
    channel = connection.channel()
    channel.queue_declare(queue=MANAGER_QUEUE, durable=True)

    message = {
        "message_id": str(time.time()),
        "sender": "FrontendUI",
        "receiver": "ManagerAgent",  # ManagerAgent name
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "type": msg_type,  # e.g. "NEW_REQUIREMENT", "UPDATE_REQUIREMENT", "CLARIFICATION_RESPONSE"
        "payload": payload
    }
    channel.basic_publish(
        exchange="",
        routing_key=MANAGER_QUEUE,
        body=json.dumps(message),
        properties=pika.BasicProperties(delivery_mode=2),
    )
    connection.close()


# Updated HTML using Bootstrap for styling and an added "Task Execution Log" section
INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Multi-Agent Project UI</title>
  <!-- Load Bootstrap 5 from a CDN -->
  <link
    href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css"
    rel="stylesheet"
  />
  <style>
    body {
      margin-top: 40px;
      margin-bottom: 40px;
      background-color: #f8f9fa;
    }
    .clarification-request {
      background: #fff3cd;
      padding: 15px;
      border: 1px solid #ffeeba;
      border-radius: 5px;
    }
    pre {
      white-space: pre-wrap; /* Allow text wrapping in <pre> */
      word-wrap: break-word;
    }
  </style>
</head>
<body>
<div class="container">

  <h1 class="mb-4">Project Frontend UI</h1>

  {% if clarification_request %}
  <!-- Clarification Section -->
  <div class="clarification-request mb-4">
    <h3>Clarification Request</h3>
    <p><strong>Requirement needing clarification:</strong></p>
    <pre>{{ clarification_request.requirement }}</pre>
    <p><strong>Clarification Questions:</strong></p>
    <ul>
      {% for question in clarification_request.clarifications %}
      <li>{{ question }}</li>
      {% endfor %}
    </ul>
    <form method="POST" action="/submit_clarification_response">
      <div class="mb-3">
        <label for="clarification_answer" class="form-label">Your Answer:</label>
        <textarea id="clarification_answer" name="clarification_answer" class="form-control" rows="3"></textarea>
      </div>
      <button type="submit" class="btn btn-warning">Submit Clarification Response</button>
    </form>
  </div>
  {% endif %}

  <div class="row">
    <div class="col-lg-6">
      <!-- Form to submit a new requirement -->
      <div class="card mb-4">
        <div class="card-body">
          <h4 class="card-title">Submit New Requirement</h4>
          <form method="POST" action="/submit_requirement">
            <div class="mb-3">
              <label for="requirement" class="form-label">Requirement Description:</label>
              <textarea id="requirement" name="requirement" class="form-control" rows="3"></textarea>
            </div>
            <button type="submit" class="btn btn-primary">Submit New Requirement</button>
          </form>
        </div>
      </div>

      <!-- Form to update/clarify an existing requirement -->
      <div class="card mb-4">
        <div class="card-body">
          <h4 class="card-title">Update/Clarify Existing Requirement</h4>
          <form method="POST" action="/update_requirement">
            <div class="mb-3">
              <label for="updateRequirement" class="form-label">Reference or Summary of Requirement:</label>
              <textarea id="updateRequirement" name="requirement" class="form-control" rows="2"></textarea>
            </div>
            <div class="mb-3">
              <label for="clarification" class="form-label">Clarification Text (optional):</label>
              <textarea id="clarification" name="clarification" class="form-control" rows="2"></textarea>
            </div>
            <button type="submit" class="btn btn-secondary">Submit Update</button>
          </form>
        </div>
      </div>
    </div> <!-- end col-lg-6 -->

    <div class="col-lg-6">
      <!-- Section to view list of projects on the File Server -->
      <div class="card mb-4">
        <div class="card-body">
          <h4 class="card-title">List Projects (File Server)</h4>
          <p>Click to list contents of the base upload directory.</p>
          <a class="btn btn-info mb-2" href="/list_projects">Refresh Project List</a>
          {% if projects %}
          <ul class="list-group">
            {% for p in projects %}
            <li class="list-group-item">{{ p }}</li>
            {% endfor %}
          </ul>
          {% endif %}
        </div>
      </div>

      <!-- Form to view a project's status (status.md, etc.) -->
      <div class="card mb-4">
        <div class="card-body">
          <h4 class="card-title">View Project Status</h4>
          <form method="GET" action="/view_project_status">
            <div class="mb-3">
              <label for="projectPath" class="form-label">File Server Path (e.g. uploads/project_1234/status.md):</label>
              <input type="text" name="projectPath" id="projectPath" class="form-control" />
            </div>
            <button type="submit" class="btn btn-success">View Status File</button>
          </form>
          {% if status_file_content %}
          <h5 class="mt-3">Status File Content:</h5>
          <pre>{{ status_file_content }}</pre>
          {% endif %}
        </div>
      </div>
    </div> <!-- end col-lg-6 -->
  </div> <!-- end row -->

  <!-- Task Execution Log Section -->
  <div class="card">
    <div class="card-body">
      <h4 class="card-title">Task Execution Log</h4>
      <p class="small text-muted">Here you can see the recent updates or results sent by the ManagerAgent (e.g., from DeveloperAgent tasks).</p>
      {% if task_executions and task_executions|length > 0 %}
        <ul class="list-group">
          {% for item in task_executions %}
          <li class="list-group-item">
            <strong>Timestamp:</strong> {{ item.timestamp }} <br/>
            <strong>Message Type:</strong> {{ item.type }} <br/>
            <strong>Payload:</strong>
            <pre>{{ item.payload | tojson(indent=2) }}</pre>
          </li>
          {% endfor %}
        </ul>
      {% else %}
        <p>No task execution logs yet.</p>
      {% endif %}
    </div>
  </div>

</div> <!-- end container -->

<!-- Bootstrap JS (optional for advanced components, but included for completeness) -->
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
"""

@app.route("/")
def index():
    """
    Renders the main UI with:
      - Pending clarification (if any)
      - Forms for new or updated requirements
      - List of projects from the file server
      - Option to view status of a project
      - Logs of recent task executions
    """
    projects = []
    try:
        list_url = f"{FILE_SERVER_BASE_URL}/list_directory"
        resp = requests.get(list_url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        projects = data.get("contents", [])
    except Exception as e:
        print(f"Error listing default directory: {e}")

    # We'll pass the in-memory tasks log to the template
    # but transform it slightly to include a "type" and "timestamp" for readability.
    display_tasks = []
    for t in task_executions_log:
        display_tasks.append({
            "type": t.get("type", ""),
            "timestamp": t.get("timestamp", ""),
            "payload": t.get("payload", {})
        })

    return render_template_string(
        INDEX_HTML,
        projects=projects,
        clarification_request=pending_clarification,
        task_executions=display_tasks
    )


@app.route("/submit_requirement", methods=["POST"])
def submit_requirement():
    """
    Publishes a NEW_REQUIREMENT message to the ManagerAgent via RabbitMQ.
    """
    requirement_text = request.form.get("requirement", "").strip()
    if not requirement_text:
        return "Requirement text is empty!", 400

    payload = {"requirement": requirement_text}
    publish_to_manager_agent_queue("NEW_REQUIREMENT", payload)
    return (
        "New requirement submitted!<br/><br/>"
        '<a href="/">Go Back</a>'
    )


@app.route("/update_requirement", methods=["POST"])
def update_requirement():
    """
    Publishes an UPDATE_REQUIREMENT message to the ManagerAgent via RabbitMQ,
    including optional clarifications.
    """
    requirement_text = request.form.get("requirement", "").strip()
    clarification_text = request.form.get("clarification", "").strip()

    if not requirement_text and not clarification_text:
        return "No requirement or clarification provided!", 400

    payload = {
        "requirement": requirement_text,
        "clarification": clarification_text
    }
    publish_to_manager_agent_queue("UPDATE_REQUIREMENT", payload)
    return (
        "Requirement update/clarification submitted!<br/><br/>"
        '<a href="/">Go Back</a>'
    )


@app.route("/submit_clarification_response", methods=["POST"])
def submit_clarification_response():
    """
    Reads the user's answer to a pending clarification request and sends a
    CLARIFICATION_RESPONSE message to the ManagerAgent via RabbitMQ.
    After sending, the pending clarification is cleared.
    """
    global pending_clarification
    if not pending_clarification:
        return "No pending clarification request to respond to.", 400

    # Use the original requirement from the clarification request.
    requirement_text = pending_clarification.get("requirement", "")
    # Get the answer from the form.
    clarification_answer = request.form.get("clarification_answer", "").strip()

    if not clarification_answer:
        return "Please provide an answer before submitting.", 400

    payload = {
        "requirement": requirement_text,
        "clarification": clarification_answer
    }
    publish_to_manager_agent_queue("CLARIFICATION_RESPONSE", payload)
    # Clear the pending clarification after response.
    pending_clarification = None
    return (
        "Clarification response submitted!<br/><br/>"
        '<a href="/">Go Back</a>'
    )


@app.route("/list_projects")
def list_projects():
    """
    Calls the file server /list_directory endpoint to list the base directory (uploads).
    Renders the same index template with an updated project list.
    """
    projects = []
    try:
        list_url = f"{FILE_SERVER_BASE_URL}/list_directory"
        resp = requests.get(list_url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        projects = data.get("contents", [])
    except Exception as e:
        print(f"Error listing projects: {e}")

    # Also include the current tasks log in the context
    display_tasks = []
    for t in task_executions_log:
        display_tasks.append({
            "type": t.get("type", ""),
            "timestamp": t.get("timestamp", ""),
            "payload": t.get("payload", {})
        })

    return render_template_string(
        INDEX_HTML,
        projects=projects,
        clarification_request=pending_clarification,
        task_executions=display_tasks
    )


@app.route("/view_project_status", methods=["GET"])
def view_project_status():
    """
    Reads a status file (e.g., status.md or developmentstatus.md) from the File Server.
    The user provides 'projectPath' (e.g. 'uploads/xyz/status.md').
    """
    project_path = request.args.get("projectPath", "").strip()
    if not project_path:
        return "Please provide a valid status file path!", 400

    content = ""
    try:
        read_url = f"{FILE_SERVER_BASE_URL}/read_file"
        resp = requests.get(read_url, params={"path": project_path}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content", "")
    except Exception as e:
        content = f"Error reading status file: {e}"

    # Re-fetch the uploads folder list for the sidebar
    projects = []
    try:
        list_url = f"{FILE_SERVER_BASE_URL}/list_directory"
        r2 = requests.get(list_url, timeout=5)
        r2.raise_for_status()
        d2 = r2.json()
        projects = d2.get("contents", [])
    except Exception:
        pass

    # Also include the current tasks log
    display_tasks = []
    for t in task_executions_log:
        display_tasks.append({
            "type": t.get("type", ""),
            "timestamp": t.get("timestamp", ""),
            "payload": t.get("payload", {})
        })

    return render_template_string(
        INDEX_HTML,
        projects=projects,
        clarification_request=pending_clarification,
        status_file_content=content,
        task_executions=display_tasks
    )


@app.route("/receive_clarification_request", methods=["POST"])
def receive_clarification_request():
    """
    Allows the ManagerAgent to send a clarification request to the front end.
    Expects JSON:
      {
        "requirement": "...",
        "clarifications": ["...","..."],
        "reason": "short explanation"
      }
    """
    global pending_clarification
    try:
        data = request.get_json()
        # Minimal validation
        if not all(k in data for k in ("requirement", "clarifications", "reason")):
            return jsonify({"error": "Invalid payload; missing keys."}), 400

        pending_clarification = data
        return jsonify({"status": "Clarification request received."}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/receive_task_execution", methods=["POST"])
def receive_task_execution():
    """
    Allows the ManagerAgent to forward a TASK_EXECUTION (or other messages) to this frontend.
    We'll store it in 'task_executions_log' so it can be displayed in the UI.
    """
    global task_executions_log
    try:
        data = request.get_json()
        # We store the entire message, but typically you'd store just essential fields
        # to avoid too large memory usage in production.
        # We'll keep it minimal for demonstration.
        # Ensure that 'type' and 'payload' are present, but not strictly enforced here.
        msg = {
            "type": data.get("type", "UNKNOWN"),
            "payload": data.get("payload", {}),
            "timestamp": data.get("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ"))
        }
        task_executions_log.insert(0, msg)  # Insert at front so newest appear first
        return jsonify({"status": "TASK_EXECUTION received"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
