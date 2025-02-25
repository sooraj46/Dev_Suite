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
                <a href="#" class="list-group-item list-group-item-action project-item" data-project-path="uploads/{{ p }}">
                  <i class="fas fa-folder me-2 text-warning"></i>{{ p }}
                </a>
                {% endfor %}
              </div>
              {% else %}
              <p class="text-muted"><i class="fas fa-info-circle me-1"></i>No projects found.</p>
              {% endif %}
            </div>
          </div>
        </div>
        
        <div class="col-md-7">
          <!-- Form to view a project's status -->
          <div class="card">
            <div class="card-header">
              <h5 class="mb-0"><i class="fas fa-file-alt me-2 text-success"></i>Project Status</h5>
            </div>
            <div class="card-body">
              <form method="GET" action="/view_project_status">
                <div class="input-group mb-3">
                  <span class="input-group-text">Path:</span>
                  <input type="text" name="projectPath" id="projectPath" class="form-control" 
                    placeholder="uploads/project_1234/status.md" />
                  <button type="submit" class="btn btn-success">
                    <i class="fas fa-search me-1"></i> View
                  </button>
                </div>
              </form>
              
              {% if status_file_content %}
              <div class="card mt-3">
                <div class="card-header bg-light">
                  Status File Content
                </div>
                <div class="card-body">
                  <pre>{{ status_file_content }}</pre>
                </div>
              </div>
              {% endif %}
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
            <div class="btn-group">
              <button class="btn btn-sm btn-outline-secondary filter-btn active" data-filter="all">All</button>
              <button class="btn btn-sm btn-outline-secondary filter-btn" data-filter="NEW_REQUIREMENT">Requirements</button>
              <button class="btn btn-sm btn-outline-secondary filter-btn" data-filter="TASK_EXECUTION">Tasks</button>
              <button class="btn btn-sm btn-outline-secondary filter-btn" data-filter="CLARIFICATION">Clarifications</button>
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
                <div class="list-group-item list-group-item-action task-item" data-type="{{ item.type }}">
                  <div class="d-flex justify-content-between align-items-start">
                    <div>
                      <span class="badge 
                        {% if 'TASK_EXECUTION' in item.type %}bg-primary
                        {% elif 'CLARIFICATION' in item.type %}bg-warning
                        {% elif 'ERROR' in item.type %}bg-danger
                        {% elif 'COMPLETE' in item.type %}bg-success
                        {% else %}bg-secondary{% endif %} me-2">
                        {{ item.type }}
                      </span>
                      <span class="text-muted small">{{ item.timestamp }}</span>
                    </div>
                    
                    <div class="feedback-container">
                      <div class="feedback-stars" data-id="{{ item.message_id }}">
                        {% for i in range(5) %}
                        <i class="far fa-star" data-rating="{{ i+1 }}"></i>
                        {% endfor %}
                      </div>
                    </div>
                  </div>
                  
                  <div class="mt-2">
                    <span class="text-primary">{{ item.sender }}</span> → <span class="text-info">{{ item.receiver }}</span>
                  </div>
                  
                  <div class="mt-2">
                    <span class="badge bg-light text-dark me-2">Status: {{ item.status }}</span>
                    {% if item.reason != "N/A" %}
                    <span class="badge bg-light text-dark">Reason: {{ item.reason }}</span>
                    {% endif %}
                  </div>
                  
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
  filterButtons.forEach(btn => {
    btn.addEventListener('click', function() {
      // Update active button
      filterButtons.forEach(b => b.classList.remove('active'));
      this.classList.add('active');
      
      const filter = this.getAttribute('data-filter');
      const taskItems = document.querySelectorAll('.task-item');
      
      taskItems.forEach(item => {
        if (filter === 'all') {
          item.style.display = 'block';
        } else {
          const itemType = item.getAttribute('data-type');
          if (itemType.includes(filter)) {
            item.style.display = 'block';
          } else {
            item.style.display = 'none';
          }
        }
      });
    });
  });
  
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
        // Turn on auto-refresh (every 10 seconds)
        refreshStatus.textContent = 'Auto-refresh: ON';
        refreshIndicator.style.display = 'inline-block';
        refreshInterval = setInterval(function() {
          // Reload the current page
          window.location.reload();
        }, 10000); // 10 seconds
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
    Now includes feedback tracking.
    """
    global task_executions_log
    try:
        data = request.get_json()
        # We store the entire message, but typically you'd store just essential fields
        # to avoid too large memory usage in production.
        # We'll keep it minimal for demonstration.
        # Ensure that 'type' and 'payload' are present, but not strictly enforced here.
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
            "feedback_rating": data.get("feedback_rating", 0)
        }
        
        # Limit log size to prevent memory issues (keep last 50 entries)
        if len(task_executions_log) >= 50:
            task_executions_log.pop()  # Remove oldest entry
            
        task_executions_log.insert(0, msg)  # Insert at front so newest appear first
        return jsonify({"status": "TASK_EXECUTION received"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
