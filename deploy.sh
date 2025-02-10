#!/bin/bash
# -------------------------------------------------------------
# Deployment Script for Dev_Suite Microservices
# This script starts all services in the background.
#
# Make sure you have:
#   - Activated your virtual environment (e.g., source venv/bin/activate)
#   - Installed all dependencies (pip install -r requirements.txt)
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh
# -------------------------------------------------------------

# Function to start a service and print its PID.
start_service() {
  local service_path="$1"
  local script="$2"
  local log_file="$3"

  echo "Starting service: $script in $service_path ..."
  cd "$service_path" || exit 1
  nohup python3 "$script" > "$log_file" 2>&1 &
  local pid=$!
  echo "$script started with PID $pid (log: $service_path/$log_file)"
  cd - > /dev/null || exit 1
}

# Optional: Warn if no virtual environment is active.
if [ -z "$VIRTUAL_ENV" ]; then
  echo "WARNING: No virtual environment active. Run 'source venv/bin/activate' before deploying."
fi

# Start Agent Registry
start_service "./Dev_Suite/AgentRegistry" "agentregistry.py" "agentregistry.log"

# Start File Server
start_service "./Dev_Suite/FileServer" "fileserver.py" "fileserver.log"

# Start Git Service
start_service "./Dev_Suite/GitService" "gitservice.py" "gitservice.log"

# Start Developer Agent (Agent Service)
start_service "./Dev_Suite/AgentService" "developeragent.py" "developeragent.log"

# Start Manager Agent (also in AgentService folder)
# (We assume manageragent.py is also in the AgentService directory)
start_service "./Dev_Suite/AgentService" "manageragent.py" "manageragent.log"

# Start Frontend Service
start_service "./Dev_Suite/FrontendService" "frontend_app.py" "frontend_app.log"

echo "-------------------------------------------------------------"
echo "Deployment complete. All services are running in the background."
echo "Check individual log files for details."
echo "-------------------------------------------------------------"