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

# Added endpoint to handle feedback submission
@app.route("/submit_feedback", methods=["POST"])
def submit_feedback():
    """
    Submit user feedback for a specific task.
    Expects a JSON payload with:
    {
        "message_id": "unique_id",
        "rating": 1-5,
        "feedback_text": "Optional feedback text"
    }
    """
    global task_executions_log
    try:
        data = request.get_json()
        message_id = data.get("message_id")
        rating = data.get("rating")
        feedback_text = data.get("feedback_text", "")
        
        if not message_id or rating is None:
            return jsonify({"error": "Missing required fields"}), 400
            
        # Find the task entry and update it
        for task in task_executions_log:
            if task.get("message_id") == message_id:
                task["feedback_rating"] = rating
                task["feedback"] = feedback_text
                
                # Optionally forward this feedback to the ManagerAgent
                payload = {
                    "message_id": message_id,
                    "rating": rating,
                    "feedback": feedback_text
                }
                publish_to_manager_agent_queue("FEEDBACK", payload)
                break
                
        return jsonify({"status": "Feedback submitted successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Dev Suite Dashboard</title>
  <!-- Load Bootstrap 5 from a CDN -->
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet" />
  <!-- Font Awesome for icons -->
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" />
  <style>
    body {
      margin-top: 40px;
      margin-bottom: 40px;
      background-color: #f8f9fa;
    }
    .clarification-request {
      background: #fff3cd;
      padding: 15px;
      border-left: 5px solid #ffc107;
      border-radius: 5px;
      box-shadow: 0 2px 5px rgba(0,0,0,0.1);
    }
    pre {
      white-space: pre-wrap; /* Allow text wrapping in <pre> */
      word-wrap: break-word;
      background-color: #f5f5f5;
      padding: 10px;
      border-radius: 4px;
    }
    .nav-tabs .nav-link {
      cursor: pointer;
    }
    .task-item {
      transition: all 0.2s ease-in-out;
    }
    .task-item:hover {
      transform: translateY(-2px);
      box-shadow: 0 4px 8px rgba(0,0,0,0.1);
    }
    .status-badge {
      position: absolute;
      top: 10px;
      right: 10px;
    }
    .feedback-stars {
      color: #ffc107;
      cursor: pointer;
    }
    .badge {
      font-size: 0.8rem;
    }
    .navbar {
      margin-bottom: 20px;
      background-color: #343a40;
      color: white;
    }
    .card {
      box-shadow: 0 2px 5px rgba(0,0,0,0.1);
      margin-bottom: 20px;
    }
    .card-header {
      background-color: #f8f9fa;
      border-bottom: 1px solid rgba(0,0,0,0.125);
    }
    .auto-refresh {
      margin-left: 10px;
      cursor: pointer;
    }
    #refreshIndicator {
      display: none;
      animation: spin 1s linear infinite;
    }
    @keyframes spin {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }
  </style>
</head>
<body>
<nav class="navbar navbar-expand-lg navbar-dark">
  <div class="container">
    <a class="navbar-brand" href="/"><i class="fas fa-robot me-2"></i>Dev Suite Dashboard</a>
    <button class="auto-refresh btn btn-sm btn-outline-light" id="autoRefreshToggle" title="Toggle auto-refresh">
      <i class="fas fa-sync-alt me-1"></i><span id="refreshStatus">Auto-refresh: OFF</span>
      <i class="fas fa-spinner fa-spin ms-1" id="refreshIndicator"></i>
    </button>
  </div>
</nav>

<div class="container">
  {% if clarification_request %}
  <!-- Clarification Section (Priority Alert) -->
  <div class="alert alert-warning d-flex align-items-center mb-4" role="alert">
    <i class="fas fa-exclamation-triangle me-2"></i>
    <div>
      <strong>Clarification Required!</strong> Please provide additional information below.
    </div>
  </div>

  <div class="clarification-request mb-4">
    <h3><i class="fas fa-question-circle me-2"></i>Clarification Request</h3>
    <div class="card">
      <div class="card-header">
        <strong>Requirement needing clarification:</strong>
      </div>
      <div class="card-body">
        <pre>{{ clarification_request.requirement }}</pre>
      </div>
    </div>
    
    <h5 class="mt-3"><i class="fas fa-list-ul me-2"></i>Clarification Questions:</h5>
    <ul class="list-group mb-3">
      {% for question in clarification_request.clarifications %}
      <li class="list-group-item">
        <i class="fas fa-angle-right me-2 text-primary"></i>{{ question }}
      </li>
      {% endfor %}
    </ul>
    
    <form method="POST" action="/submit_clarification_response">
      <div class="mb-3">
        <label for="clarification_answer" class="form-label"><i class="fas fa-pen me-2"></i>Your Response:</label>
        <textarea id="clarification_answer" name="clarification_answer" class="form-control" rows="4" 
          placeholder="Provide details to help the development team understand your requirements better..."></textarea>
      </div>
      <button type="submit" class="btn btn-warning">
        <i class="fas fa-paper-plane me-1"></i> Submit Clarification
      </button>
    </form>
  </div>
  {% endif %}

  <!-- Main Tabs Navigation -->
  <ul class="nav nav-tabs" id="mainTabs" role="tablist">
    <li class="nav-item" role="presentation">
      <button class="nav-link active" id="requirements-tab" data-bs-toggle="tab" data-bs-target="#requirements" type="button" role="tab">
        <i class="fas fa-tasks me-1"></i> Requirements
      </button>
    </li>
    <li class="nav-item" role="presentation">
      <button class="nav-link" id="projects-tab" data-bs-toggle="tab" data-bs-target="#projects" type="button" role="tab">
        <i class="fas fa-folder me-1"></i> Projects
      </button>
    </li>
    <li class="nav-item" role="presentation">
      <button class="nav-link" id="activity-tab" data-bs-toggle="tab" data-bs-target="#activity" type="button" role="tab">
        <i class="fas fa-chart-line me-1"></i> Activity Log
      </button>
    </li>
  </ul>
  
  <!-- Tab Content -->
  <div class="tab-content" id="mainTabsContent">
    <!-- Requirements Tab -->
    <div class="tab-pane fade show active" id="requirements" role="tabpanel">
      <div class="row mt-4">
        <div class="col-md-6">
          <!-- Form to submit a new requirement -->
          <div class="card">
            <div class="card-header">
              <h5 class="mb-0"><i class="fas fa-plus-circle me-2 text-success"></i>Submit New Requirement</h5>
            </div>
            <div class="card-body">
              <form method="POST" action="/submit_requirement">
                <div class="mb-3">
                  <label for="requirement" class="form-label">Requirement Description:</label>
                  <textarea id="requirement" name="requirement" class="form-control" rows="4" 
                    placeholder="Describe your feature request or bug fix needed..."></textarea>
                </div>
                <div class="mb-3">
                  <label for="priority" class="form-label">Priority:</label>
                  <select class="form-select" id="priority" name="priority">
                    <option value="low">Low</option>
                    <option value="medium" selected>Medium</option>
                    <option value="high">High</option>
                    <option value="critical">Critical</option>
                  </select>
                </div>
                <button type="submit" class="btn btn-primary">
                  <i class="fas fa-paper-plane me-1"></i> Submit Requirement
                </button>
              </form>
            </div>
          </div>
        </div>
        
        <div class="col-md-6">
          <!-- Form to update/clarify an existing requirement -->
          <div class="card">
            <div class="card-header">
              <h5 class="mb-0"><i class="fas fa-edit me-2 text-primary"></i>Update Requirement</h5>
            </div>
            <div class="card-body">
              <form method="POST" action="/update_requirement">
                <div class="mb-3">
                  <label for="updateRequirement" class="form-label">Reference Requirement:</label>
                  <textarea id="updateRequirement" name="requirement" class="form-control" rows="2" 
                    placeholder="Briefly reference the original requirement..."></textarea>
                </div>
                <div class="mb-3">
                  <label for="clarification" class="form-label">Additional Details:</label>
                  <textarea id="clarification" name="clarification" class="form-control" rows="3" 
                    placeholder="Provide additional details, clarifications, or changes..."></textarea>
                </div>
                <button type="submit" class="btn btn-outline-primary">
                  <i class="fas fa-sync-alt me-1"></i> Update Requirement
                </button>
              </form>
            </div>
          </div>
        </div>
      </div>
    </div>
    
    <!-- Projects Tab -->
    <div class="tab-pane fade" id="projects" role="tabpanel">
      <div class="row mt-4">
        <div class="col-md-5">
          <!-- Section to view list of projects on the File Server -->
          <div class="card">
            <div class="card-header d-flex justify-content-between align-items-center">
              <h5 class="mb-0"><i class="fas fa-folder-open me-2 text-info"></i>Projects</h5>
              <a class="btn btn-sm btn-info" href="/list_projects">
                <i class="fas fa-sync-alt me-1"></i> Refresh
              </a>
            </div>
            <div class="card-body">
              {% if projects %}
              <div class="list-group">
                {% for p in projects %}
                <div class="list-group-item mb-2 border-left">
                  <div class="d-flex justify-content-between align-items-center">
                    <h6>
                      <i class="fas fa-folder me-2 text-warning"></i>{{ p.name }}
                      <span class="badge 
                        {% if p.state == 'completed' %}bg-success
                        {% elif p.state == 'testing' %}bg-warning
                        {% elif p.state == 'development' %}bg-info
                        {% elif p.state == 'assigned' %}bg-primary
                        {% elif p.state == 'initialized' %}bg-secondary
                        {% else %}bg-secondary{% endif %}">
                        {{ p.state }}
                      </span>
                    </h6>
                    <div class="dropdown">
                      <button class="btn btn-sm btn-outline-secondary dropdown-toggle" type="button" id="projectMenu{{ loop.index }}" data-bs-toggle="dropdown" aria-expanded="false">
                        <i class="fas fa-ellipsis-v"></i>
                      </button>
                      <ul class="dropdown-menu" aria-labelledby="projectMenu{{ loop.index }}">
                        <li><a class="dropdown-item" href="/view_project_status?projectName={{ p.name }}&fileType=status">
                          <i class="fas fa-file-alt me-2"></i>View Status</a></li>
                        <li><a class="dropdown-item" href="/view_project_status?projectName={{ p.name }}&fileType=development">
                          <i class="fas fa-code me-2"></i>View Dev Status</a></li>
                        <li><a class="dropdown-item" href="/view_project_status?projectName={{ p.name }}&fileType=test">
                          <i class="fas fa-vial me-2"></i>View Test Results</a></li>
                        <li><a class="dropdown-item" href="/view_project_status?projectName={{ p.name }}&fileType=requirements">
                          <i class="fas fa-clipboard-list me-2"></i>View Requirements</a></li>
                      </ul>
                    </div>
                  </div>
                  <div class="small text-muted mt-2">
                    {% if p.state == 'development' %}
                    <i class="fas fa-code me-1"></i> In development
                    {% elif p.state == 'testing' %}
                    <i class="fas fa-vial me-1"></i> Testing in progress
                    {% elif p.state == 'completed' %}
                    <i class="fas fa-check-circle me-1"></i> Project completed
                    {% elif p.state == 'initialized' %}
                    <i class="fas fa-hourglass-start me-1"></i> Awaiting processing
                    {% elif p.state == 'assigned' %}
                    <i class="fas fa-tasks me-1"></i> Task assigned
                    {% else %}
                    <i class="fas fa-question-circle me-1"></i> Unknown status
                    {% endif %}
                  </div>
                </div>
                {% endfor %}
              </div>
              {% else %}
              <p class="text-muted"><i class="fas fa-info-circle me-1"></i>No projects found.</p>
              {% endif %}
            </div>
          </div>
        </div>
        
        <div class="col-md-7">
          <!-- Project details view -->
          <div class="card">
            <div class="card-header d-flex justify-content-between align-items-center">
              <h5 class="mb-0">
                <i class="fas fa-file-alt me-2 text-success"></i>
                {% if current_project_name %}Project: {{ current_project_name }}{% else %}Project Details{% endif %}
              </h5>
              
              {% if current_project_name %}
              <div class="btn-group">
                <a href="/view_project_status?projectName={{ current_project_name }}&fileType=status" class="btn btn-sm btn-outline-primary">Status</a>
                <a href="/view_project_status?projectName={{ current_project_name }}&fileType=development" class="btn btn-sm btn-outline-info">Dev Info</a>
                <a href="/view_project_status?projectName={{ current_project_name }}&fileType=test" class="btn btn-sm btn-outline-warning">Tests</a>
                <a href="/view_project_status?projectName={{ current_project_name }}&fileType=requirements" class="btn btn-sm btn-outline-dark">Requirements</a>
              </div>
              {% endif %}
            </div>
            <div class="card-body">
              {% if not current_project_name and not status_file_content %}
                <p class="text-center text-muted py-4">
                  <i class="fas fa-info-circle fa-2x mb-3"></i><br>
                  Select a project from the list to view its details
                </p>
              {% else %}
                {% if status_file_content %}
                <div class="card">
                  <div class="card-header bg-light d-flex justify-content-between align-items-center">
                    <div>
                      {% if current_project_path and 'status.md' in current_project_path %}
                        <i class="fas fa-file-alt me-1 text-primary"></i> Status File
                      {% elif current_project_path and 'developmentstatus.md' in current_project_path %}
                        <i class="fas fa-code me-1 text-info"></i> Development Log
                      {% elif current_project_path and 'test_results.md' in current_project_path %}
                        <i class="fas fa-vial me-1 text-warning"></i> Test Results
                      {% elif current_project_path and 'requirements.md' in current_project_path %}
                        <i class="fas fa-clipboard-list me-1 text-dark"></i> Requirements
                      {% else %}
                        <i class="fas fa-file me-1"></i> File Content
                      {% endif %}
                    </div>
                    
                    {% if current_project_path %}
                    <span class="small text-muted">{{ current_project_path }}</span>
                    {% endif %}
                  </div>
                  <div class="card-body">
                    <pre class="p-3 bg-light rounded">{{ status_file_content }}</pre>
                  </div>
                </div>
                {% endif %}
                
                {% if current_project_name %}
                  {% set project_logs = [] %}
                  {% for task in task_executions %}
                    {% if task.project_name == current_project_name %}
                      {% set _ = project_logs.append(task) %}
                    {% endif %}
                  {% endfor %}
                  
                  {% if project_logs %}
                  <div class="card mt-3">
                    <div class="card-header bg-light">
                      <i class="fas fa-history me-1"></i> Project Activity ({{ project_logs|length }} events)
                    </div>
                    <div class="card-body p-0">
                      <div class="list-group list-group-flush">
                        {% for log in project_logs %}
                        <div class="list-group-item py-2 px-3">
                          <div class="d-flex justify-content-between align-items-center">
                            <span class="badge 
                              {% if 'TASK_EXECUTION' in log.type %}bg-primary
                              {% elif 'CLARIFICATION' in log.type %}bg-warning
                              {% elif 'PROGRESS_UPDATE' in log.type %}bg-info
                              {% elif 'ERROR' in log.type %}bg-danger
                              {% elif 'COMPLETE' in log.type %}bg-success
                              {% else %}bg-secondary{% endif %} me-2">
                              {{ log.type }}
                            </span>
                            <small class="text-muted">{{ log.timestamp }}</small>
                          </div>
                          <div class="small mt-1">
                            {{ log.sender }} → {{ log.receiver }}
                          </div>
                          {% if log.payload.message %}
                          <div class="mt-1 small">
                            <i class="fas fa-comment-alt me-1"></i> {{ log.payload.message }}
                          </div>
                          {% endif %}
                        </div>
                        {% endfor %}
                      </div>
                    </div>
                  </div>
                  {% endif %}
                {% endif %}
              {% endif %}
              
              <form method="GET" action="/view_project_status" class="mt-3">
                <div class="input-group">
                  <span class="input-group-text">Custom Path:</span>
                  <input type="text" name="projectPath" id="projectPath" class="form-control" 
                    placeholder="uploads/project_1234/custom_file.md" />
                  <button type="submit" class="btn btn-secondary">
                    <i class="fas fa-search me-1"></i> View
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      </div>
    </div>
    
    <!-- Activity Log Tab -->
    <div class="tab-pane fade" id="activity" role="tabpanel">
      <div class="mt-4">
        <div class="card">
          <div class="card-header d-flex justify-content-between align-items-center">
            <h5 class="mb-0"><i class="fas fa-history me-2 text-primary"></i>Development Activity Log</h5>
            <div class="d-flex gap-2">
              <div class="btn-group me-2">
                <button class="btn btn-sm btn-outline-secondary filter-btn active" data-filter="all">All</button>
                <button class="btn btn-sm btn-outline-secondary filter-btn" data-filter="NEW_REQUIREMENT">Requirements</button>
                <button class="btn btn-sm btn-outline-secondary filter-btn" data-filter="TASK_EXECUTION">Tasks</button>
                <button class="btn btn-sm btn-outline-secondary filter-btn" data-filter="CLARIFICATION">Clarifications</button>
              </div>
              
              <select id="projectFilter" class="form-select form-select-sm" style="max-width: 180px;">
                <option value="all">All Projects</option>
                {% for p in projects %}
                <option value="{{ p.name }}">{{ p.name }}</option>
                {% endfor %}
              </select>
            </div>
          </div>
          <div class="card-body">
            {% if task_executions and task_executions|length > 0 %}
              <div class="d-flex justify-content-end mb-3">
                <div class="input-group" style="max-width: 300px;">
                  <span class="input-group-text"><i class="fas fa-search"></i></span>
                  <input type="text" id="searchActivity" class="form-control" placeholder="Search in logs...">
                </div>
              </div>
              
              <div class="list-group">
                {% for item in task_executions %}
                <div class="list-group-item list-group-item-action task-item" data-type="{{ item.type }}" data-project="{{ item.project_name }}">
                  <div class="d-flex justify-content-between align-items-start">
                    <div>
                      <span class="badge 
                        {% if 'TASK_EXECUTION' in item.type %}bg-primary
                        {% elif 'CLARIFICATION' in item.type %}bg-warning
                        {% elif 'PROGRESS_UPDATE' in item.type %}bg-info
                        {% elif 'ERROR' in item.type %}bg-danger
                        {% elif 'COMPLETE' in item.type %}bg-success
                        {% else %}bg-secondary{% endif %} me-2">
                        {{ item.type }}
                      </span>
                      <span class="text-muted small">{{ item.timestamp }}</span>
                    </div>
                    
                    <div class="feedback-container">
                      {% if 'TASK_EXECUTION' in item.type %}
                      <div class="feedback-stars" data-id="{{ item.message_id }}">
                        {% for i in range(5) %}
                        <i class="far fa-star" data-rating="{{ i+1 }}"></i>
                        {% endfor %}
                      </div>
                      {% endif %}
                    </div>
                  </div>
                  
                  <div class="mt-2 d-flex justify-content-between">
                    <div>
                      <span class="text-primary">{{ item.sender }}</span> → <span class="text-info">{{ item.receiver }}</span>
                    </div>
                    {% if item.project_name %}
                    <span class="badge bg-light text-dark" data-project-name="{{ item.project_name }}">
                      <i class="fas fa-folder-open me-1"></i>{{ item.project_name }}
                    </span>
                    {% endif %}
                  </div>
                  
                  {% if 'PROGRESS_UPDATE' in item.type and item.get('progress') is not none %}
                  <div class="mt-2">
                    <div class="progress" style="height: 20px;">
                      <div class="progress-bar progress-bar-striped 
                        {% if item.payload.stage == 'error' or item.payload.stage == 'failed' %}bg-danger
                        {% elif item.payload.stage == 'complete' %}bg-success
                        {% elif item.payload.stage == 'testing' %}bg-warning
                        {% else %}bg-info{% endif %}" 
                        role="progressbar" 
                        style="width: {{ (item.get('progress', 0) * 100)|int }}%;" 
                        aria-valuenow="{{ (item.get('progress', 0) * 100)|int }}" 
                        aria-valuemin="0" 
                        aria-valuemax="100">
                        {{ (item.get('progress', 0) * 100)|int }}%
                      </div>
                    </div>
                    <div class="mt-1 small">
                      <i class="fas fa-info-circle me-1"></i>
                      <span class="fw-bold">{{ item.payload.stage|capitalize }}:</span> {{ item.payload.message }}
                    </div>
                  </div>
                  {% else %}
                  <div class="mt-2">
                    <span class="badge bg-light text-dark me-2">Status: {{ item.status }}</span>
                    {% if item.reason != "N/A" %}
                    <span class="badge bg-light text-dark">Reason: {{ item.reason }}</span>
                    {% endif %}
                  </div>
                  {% endif %}
                  
                  <a class="btn btn-sm btn-outline-secondary mt-2 toggle-payload" data-bs-toggle="collapse" 
                     href="#payload-{{ loop.index }}" role="button">
                    <i class="fas fa-chevron-down me-1"></i> Details
                  </a>
                  
                  <div class="collapse mt-2" id="payload-{{ loop.index }}">
                    <pre>{{ item.payload | tojson(indent=2) }}</pre>
                  </div>
                </div>
                {% endfor %}
              </div>
            {% else %}
              <div class="text-center py-5">
                <i class="fas fa-clipboard-list fa-3x mb-3 text-muted"></i>
                <p>No activity logs yet. Submit requirements to see development progress.</p>
              </div>
            {% endif %}
          </div>
        </div>
      </div>
    </div>
  </div>
</div> <!-- end container -->

<!-- Bootstrap JS and custom scripts -->
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
document.addEventListener('DOMContentLoaded', function() {
  // Project item click handler
  const projectItems = document.querySelectorAll('.project-item');
  projectItems.forEach(item => {
    item.addEventListener('click', function(e) {
      e.preventDefault();
      const path = this.getAttribute('data-project-path');
      document.getElementById('projectPath').value = path + '/status.md';
    });
  });
  
  // Toggle payload details
  const toggleButtons = document.querySelectorAll('.toggle-payload');
  toggleButtons.forEach(btn => {
    btn.addEventListener('click', function() {
      const icon = this.querySelector('i');
      if (icon.classList.contains('fa-chevron-down')) {
        icon.classList.replace('fa-chevron-down', 'fa-chevron-up');
      } else {
        icon.classList.replace('fa-chevron-up', 'fa-chevron-down');
      }
    });
  });
  
  // Star rating system
  const stars = document.querySelectorAll('.feedback-stars i');
  stars.forEach(star => {
    star.addEventListener('mouseover', function() {
      const rating = this.getAttribute('data-rating');
      const starsContainer = this.parentElement;
      const allStars = starsContainer.querySelectorAll('i');
      
      allStars.forEach(s => {
        const sRating = s.getAttribute('data-rating');
        if (sRating <= rating) {
          s.classList.replace('far', 'fas');
        } else {
          s.classList.replace('fas', 'far');
        }
      });
    });
    
    star.addEventListener('mouseout', function() {
      const starsContainer = this.parentElement;
      const allStars = starsContainer.querySelectorAll('i');
      const hasRating = starsContainer.getAttribute('data-user-rating');
      
      if (!hasRating) {
        allStars.forEach(s => {
          s.classList.replace('fas', 'far');
        });
      } else {
        allStars.forEach(s => {
          const sRating = s.getAttribute('data-rating');
          if (sRating <= hasRating) {
            s.classList.replace('far', 'fas');
          } else {
            s.classList.replace('fas', 'far');
          }
        });
      }
    });
    
    star.addEventListener('click', function() {
      const rating = this.getAttribute('data-rating');
      const starsContainer = this.parentElement;
      const messageId = starsContainer.getAttribute('data-id');
      
      // Store the rating
      starsContainer.setAttribute('data-user-rating', rating);
      
      // Show feedback prompt
      const listItem = starsContainer.closest('.list-group-item');
      
      // Check if feedback form already exists
      if (!listItem.querySelector('.feedback-form')) {
        const feedbackForm = document.createElement('div');
        feedbackForm.className = 'feedback-form mt-3';
        feedbackForm.innerHTML = `
          <div class="input-group">
            <input type="text" class="form-control form-control-sm feedback-text" 
                   placeholder="Additional feedback (optional)">
            <button class="btn btn-sm btn-outline-primary submit-feedback">Submit</button>
          </div>
        `;
        
        // Add after the collapse div
        const collapseDiv = listItem.querySelector('.collapse');
        collapseDiv.parentNode.insertBefore(feedbackForm, collapseDiv.nextSibling);
        
        // Add event listener to the submit button
        const submitBtn = feedbackForm.querySelector('.submit-feedback');
        submitBtn.addEventListener('click', function() {
          const feedbackText = feedbackForm.querySelector('.feedback-text').value;
          
          // Send the feedback to the server
          fetch('/submit_feedback', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({
              message_id: messageId,
              rating: parseInt(rating),
              feedback_text: feedbackText
            }),
          })
          .then(response => response.json())
          .then(data => {
            // Show success message
            feedbackForm.innerHTML = `
              <div class="alert alert-success py-2">
                <i class="fas fa-check-circle me-1"></i> Feedback submitted successfully
              </div>
            `;
            
            // Hide the success message after 3 seconds
            setTimeout(() => {
              feedbackForm.remove();
            }, 3000);
          })
          .catch(error => {
            feedbackForm.innerHTML = `
              <div class="alert alert-danger py-2">
                <i class="fas fa-exclamation-circle me-1"></i> Error submitting feedback
              </div>
            `;
          });
        });
      }
      
      // Visual feedback for stars
      const allStars = starsContainer.querySelectorAll('i');
      allStars.forEach(s => {
        const sRating = s.getAttribute('data-rating');
        if (sRating <= rating) {
          s.classList.replace('far', 'fas');
        } else {
          s.classList.replace('fas', 'far');
        }
      });
    });
  });
  
  // Filter buttons for activity log
  const filterButtons = document.querySelectorAll('.filter-btn');
  const projectFilter = document.getElementById('projectFilter');
  
  // Function to apply both filters
  function applyFilters() {
    const typeFilter = document.querySelector('.filter-btn.active').getAttribute('data-filter');
    const projectValue = projectFilter ? projectFilter.value : 'all';
    
    const taskItems = document.querySelectorAll('.task-item');
    taskItems.forEach(item => {
      // Type filter
      let typeMatch = false;
      if (typeFilter === 'all') {
        typeMatch = true;
      } else {
        const itemType = item.getAttribute('data-type');
        if (itemType && itemType.includes(typeFilter)) {
          typeMatch = true;
        }
      }
      
      // Project filter
      let projectMatch = false;
      if (projectValue === 'all') {
        projectMatch = true;
      } else {
        const projectName = item.getAttribute('data-project');
        if (projectName && projectName === projectValue) {
          projectMatch = true;
        }
      }
      
      // Apply both filters
      if (typeMatch && projectMatch) {
        item.style.display = 'block';
      } else {
        item.style.display = 'none';
      }
    });
  }
  
  // Add data-project attributes to all task items
  document.querySelectorAll('.task-item').forEach(item => {
    let projectName = null;
    const projectInfo = item.querySelector('[data-project-name]');
    if (projectInfo) {
      projectName = projectInfo.getAttribute('data-project-name');
      item.setAttribute('data-project', projectName);
    }
  });
  
  // Listen for filter button clicks
  filterButtons.forEach(btn => {
    btn.addEventListener('click', function() {
      // Update active button
      filterButtons.forEach(b => b.classList.remove('active'));
      this.classList.add('active');
      
      // Apply both filters
      applyFilters();
    });
  });
  
  // Listen for project filter changes
  if (projectFilter) {
    projectFilter.addEventListener('change', applyFilters);
  }
  
  // Add filter for progress updates
  const progressButton = document.createElement('button');
  progressButton.className = 'btn btn-sm btn-outline-secondary filter-btn';
  progressButton.setAttribute('data-filter', 'PROGRESS_UPDATE');
  progressButton.textContent = 'Progress';
  document.querySelector('.btn-group').appendChild(progressButton);
  
  // Setup live progress updates via polling
  function updateProgressBars() {
    const progressBars = document.querySelectorAll('.progress-bar');
    
    // Animate progress bars when they appear
    progressBars.forEach(bar => {
      if (!bar.classList.contains('progress-animated')) {
        bar.classList.add('progress-animated');
        const currentWidth = bar.style.width;
        bar.style.width = '0%';
        setTimeout(() => {
          bar.style.transition = 'width 0.8s ease-in-out';
          bar.style.width = currentWidth;
        }, 100);
      }
    });
  }
  
  // Call once on page load
  updateProgressBars();
  
  // Call whenever auto-refresh happens
  if (refreshToggle) {
    refreshToggle.addEventListener('click', function() {
      if (refreshInterval) {
        // Set up a callback to refresh progress bars when page updates
        const originalReload = window.location.reload;
        window.location.reload = function() {
          originalReload.apply(this, arguments);
          setTimeout(updateProgressBars, 500);
        };
      }
    });
  }
  
  // Search in activity log
  const searchInput = document.getElementById('searchActivity');
  if (searchInput) {
    searchInput.addEventListener('input', function() {
      const searchTerm = this.value.toLowerCase();
      const taskItems = document.querySelectorAll('.task-item');
      
      taskItems.forEach(item => {
        const text = item.textContent.toLowerCase();
        if (text.includes(searchTerm)) {
          item.style.display = 'block';
        } else {
          item.style.display = 'none';
        }
      });
    });
  }
  
  // Auto-refresh feature
  let refreshInterval;
  const refreshToggle = document.getElementById('autoRefreshToggle');
  const refreshStatus = document.getElementById('refreshStatus');
  const refreshIndicator = document.getElementById('refreshIndicator');
  
  if (refreshToggle) {
    refreshToggle.addEventListener('click', function() {
      if (refreshInterval) {
        // Turn off auto-refresh
        clearInterval(refreshInterval);
        refreshInterval = null;
        refreshStatus.textContent = 'Auto-refresh: OFF';
        refreshIndicator.style.display = 'none';
      } else {
        // Turn on auto-refresh (every 5 seconds for more responsive updates)
        refreshStatus.textContent = 'Auto-refresh: ON';
        refreshIndicator.style.display = 'inline-block';
        
        // Refresh immediately once
        fetch(window.location.href)
          .then(() => updateProgressBars());
        
        refreshInterval = setInterval(function() {
          // Check for updates without full page reload first
          fetch(window.location.href)
            .then(() => {
              // Only reload full page if we've been idle for a while
              window.location.reload();
              setTimeout(updateProgressBars, 500);
            });
        }, 5000); // 5 seconds
      }
    });
  }
});
</script>
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
    project_statuses = {}
    try:
        # Get list of projects
        list_url = f"{FILE_SERVER_BASE_URL}/list_directory"
        params = {"path": "uploads"}
        resp = requests.get(list_url, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        project_dirs = [item.replace("[DIR] ", "") for item in data.get("contents", []) if item.startswith("[DIR]")]
        
        # For each project, get the status info
        for project_name in project_dirs:
            status_path = f"uploads/{project_name}/status.md"
            try:
                status_url = f"{FILE_SERVER_BASE_URL}/read_file"
                status_resp = requests.get(status_url, params={"path": status_path}, timeout=5)
                if status_resp.status_code == 200:
                    status_data = status_resp.json()
                    status_content = status_data.get("content", "No status available")
                    
                    # Determine project state
                    if "Project marked as completed" in status_content:
                        state = "completed"
                    elif "testing" in status_content.lower():
                        state = "testing"
                    elif "Task assigned to" in status_content or "Assigned next task to" in status_content:
                        state = "assigned"
                    elif "generating" in status_content.lower() or "code generation" in status_content.lower():
                        state = "development"
                    elif "Project initialized" in status_content:
                        state = "initialized"
                    else:
                        state = "unknown"
                        
                    # For display in the UI
                    project_statuses[project_name] = {
                        "status_content": status_content,
                        "state": state
                    }
                else:
                    project_statuses[project_name] = {
                        "status_content": "Unable to fetch status",
                        "state": "unknown"
                    }
            except Exception as e:
                print(f"Error fetching status for project {project_name}: {e}")
                project_statuses[project_name] = {
                    "status_content": f"Error: {str(e)}",
                    "state": "error"
                }
        
        projects = [{"name": name, **status} for name, status in project_statuses.items()]
    except Exception as e:
        print(f"Error listing projects directory: {e}")

    # We'll pass the in-memory tasks log to the template
    # but transform it slightly to include a "type" and "timestamp" for readability.
    display_tasks = []
    for t in task_executions_log:
        # Associate tasks with projects if possible
        project_name = None
        if "project_config" in t.get("payload", {}) and "project_name" in t.get("payload", {}).get("project_config", {}):
            project_name = t.get("payload", {}).get("project_config", {}).get("project_name")
        elif "project_name" in t.get("payload", {}):
            project_name = t.get("payload", {}).get("project_name")
            
        display_tasks.append({
            "type": t.get("type", ""),
            "timestamp": t.get("timestamp", ""),
            "payload": t.get("payload", {}),
            "progress": t.get("progress", 0.0),
            "sender": t.get("sender", ""),
            "receiver": t.get("receiver", ""),
            "status": t.get("status", ""),
            "reason": t.get("reason", "N/A"),
            "project_name": project_name
        })

    return render_template_string(
        INDEX_HTML,
        projects=projects,
        clarification_request=pending_clarification,
        task_executions=display_tasks,
        project_statuses=project_statuses
    )


@app.route("/submit_requirement", methods=["POST"])
def submit_requirement():
    """
    Publishes a NEW_REQUIREMENT message to the ManagerAgent via RabbitMQ.
    Now includes priority field.
    """
    requirement_text = request.form.get("requirement", "").strip()
    priority = request.form.get("priority", "medium").strip()
    
    if not requirement_text:
        return "Requirement text is empty!", 400

    payload = {
        "requirement": requirement_text,
        "priority": priority
    }
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
    Calls the file server /list_directory endpoint to list the projects (uploads).
    Renders the same index template with an updated project list.
    """
    # Use the same logic as in the index route to get detailed project status
    return index()


def build_context():
    """
    Gathers the data normally passed to your Jinja template,
    including projects, statuses, clarifications, tasks, etc.
    Returns a dictionary that can be unpacked with **context
    when calling render_template_string.
    """
    global pending_clarification, task_executions_log
    
    projects = []
    project_statuses = {}

    # --- Fetch project list/status the same way you do in index() ---
    try:
        list_url = f"{FILE_SERVER_BASE_URL}/list_directory"
        params = {"path": "uploads"}
        resp = requests.get(list_url, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        project_dirs = [
            item.replace("[DIR] ", "")
            for item in data.get("contents", [])
            if item.startswith("[DIR]")
        ]

        # For each project, get the status info
        for project_name in project_dirs:
            status_path = f"uploads/{project_name}/status.md"
            try:
                status_url = f"{FILE_SERVER_BASE_URL}/read_file"
                status_resp = requests.get(status_url, params={"path": status_path}, timeout=5)

                if status_resp.status_code == 200:
                    status_data = status_resp.json()
                    status_content = status_data.get("content", "No status available")

                    # Determine project state
                    if "Project marked as completed" in status_content:
                        state = "completed"
                    elif "testing" in status_content.lower():
                        state = "testing"
                    elif "Task assigned to" in status_content or "Assigned next task to" in status_content:
                        state = "assigned"
                    elif "generating" in status_content.lower() or "code generation" in status_content.lower():
                        state = "development"
                    elif "Project initialized" in status_content:
                        state = "initialized"
                    else:
                        state = "unknown"

                    project_statuses[project_name] = {
                        "status_content": status_content,
                        "state": state
                    }
                else:
                    project_statuses[project_name] = {
                        "status_content": "Unable to fetch status",
                        "state": "unknown"
                    }
            except Exception as e:
                project_statuses[project_name] = {
                    "status_content": f"Error fetching status: {str(e)}",
                    "state": "error"
                }

        projects = [
            {"name": name, **status}
            for name, status in project_statuses.items()
        ]
    except Exception as e:
        print(f"Error listing projects directory: {e}")

    # --- Build display_tasks from task_executions_log ---
    display_tasks = []
    for t in task_executions_log:
        # Attempt to pull a project_name if present
        project_name = None
        if ("project_config" in t.get("payload", {}) and 
            "project_name" in t.get("payload", {}).get("project_config", {})):
            project_name = t["payload"]["project_config"]["project_name"]
        elif "project_name" in t.get("payload", {}):
            project_name = t["payload"]["project_name"]

        display_tasks.append({
            "type": t.get("type", ""),
            "timestamp": t.get("timestamp", ""),
            "payload": t.get("payload", {}),
            "progress": t.get("progress", 0.0),
            "sender": t.get("sender", ""),
            "receiver": t.get("receiver", ""),
            "status": t.get("status", ""),
            "reason": t.get("reason", "N/A"),
            "project_name": project_name,
            "message_id": t.get("message_id", "")
        })

    return {
        "projects": projects,
        "project_statuses": project_statuses,
        "clarification_request": pending_clarification,
        "task_executions": display_tasks
    }


@app.route("/view_project_status", methods=["GET"])
def view_project_status():
    """
    Reads a status file (like 'status.md') from the File Server
    based on either 'projectPath' or 'projectName'+'fileType'.
    Injects that file’s content into the same template used by index().
    """
    project_path = request.args.get("projectPath", "").strip()
    project_name = request.args.get("projectName", "").strip()
    file_type = request.args.get("fileType", "status").strip()

    # If project_name & file_type are provided, construct the path
    if project_name and not project_path:
        if file_type == "status":
            project_path = f"uploads/{project_name}/status.md"
        elif file_type == "development":
            project_path = f"uploads/{project_name}/developmentstatus.md"
        elif file_type == "test":
            project_path = f"uploads/{project_name}/test_results.md"
        elif file_type == "requirements":
            project_path = f"uploads/{project_name}/requirements.md"

    if not project_path:
        return "Please provide a valid status file path or project name and file type!", 400

    # Attempt to read the file
    content = ""
    try:
        read_url = f"{FILE_SERVER_BASE_URL}/read_file"
        resp = requests.get(read_url, params={"path": project_path}, timeout=5)
        
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("content", "")
        elif resp.status_code == 404 or "error" in resp.json():
            # File might not exist yet, provide a helpful message based on file type
            if file_type == "development":
                content = "Development status file has not been created yet. It will be created when code generation starts."
            elif file_type == "test":
                content = "Test results file has not been created yet. It will be created when testing is performed."
            else:
                content = f"This file ({project_path}) does not exist yet."
    except Exception as e:
        content = f"Error reading file: {e}"

    # Build the base context (same data as the index page)
    ctx = build_context()

    # Inject the “status file” content and current project info
    ctx["status_file_content"] = content
    ctx["current_project_path"] = project_path
    if project_name:
        ctx["current_project_name"] = project_name

    # Render the same template you use in index()
    return render_template_string(INDEX_HTML, **ctx)


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
    Allows the ManagerAgent to forward TASK_EXECUTION or PROGRESS_UPDATE messages to this frontend.
    We'll store it in 'task_executions_log' so it can be displayed in the UI.
    Now includes progress tracking, live updates, and feedback system.
    """
    global task_executions_log
    try:
        data = request.get_json()
        # We store the entire message, but typically you'd store just essential fields
        # to avoid too large memory usage in production.
        
        # Extract and store progress information for task tracking
        msg = {
            "message_id": data.get("message_id", str(time.time())),  
            "sender": data.get("sender", "Unknown"),
            "receiver": data.get("receiver", "FrontendUI"),
            "timestamp": data.get("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ")),
            "type": data.get("type", "UNKNOWN"),
            "payload": data.get("payload", {}),
            "status": data.get("status", "Pending"),  
            "reason": data.get("reason", "N/A"),
            "feedback": data.get("feedback", None),
            "feedback_rating": data.get("feedback_rating", 0),
            "progress": data.get("progress", None)  # Store progress value if provided
        }
        
        # For progress updates, check if we need to update an existing entry
        if data.get("type") == "PROGRESS_UPDATE" and data.get("payload", {}).get("project_name"):
            project_name = data.get("payload", {}).get("project_name")
            
            # Find and update existing project's progress entries
            for i, entry in enumerate(task_executions_log):
                if (entry.get("type") == "PROGRESS_UPDATE" and 
                    entry.get("payload", {}).get("project_name") == project_name):
                    # Update the existing entry instead of creating a new one
                    task_executions_log[i] = msg
                    return jsonify({"status": "PROGRESS_UPDATE updated"}), 200
        
        # Limit log size to prevent memory issues (keep last 50 entries)
        if len(task_executions_log) >= 50:
            task_executions_log.pop()  # Remove oldest entry
            
        task_executions_log.insert(0, msg)  # Insert at front so newest appear first
        return jsonify({"status": f"{data.get('type', 'Message')} received"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
