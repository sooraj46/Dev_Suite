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

# Start Service Manager which will start all other services
start_service "./Dev_Suite/ServiceManager" "servicemanager.py" "servicemanager.log"

# Note: The Service Manager will start the following services automatically:
# - AgentRegistry (agentregistry.py)
# - FileServer (fileserver.py)
# - GitService (gitservice.py)
# - ManagerAgent (manageragent.py)
# - DeveloperAgent (developeragent.py)
# - TestingAgent (testagent.py)
# - FrontendService (frontend_app.py)

echo "-------------------------------------------------------------"
echo "Deployment complete. All services are running in the background."
echo "Check individual log files for details."
echo "-------------------------------------------------------------"