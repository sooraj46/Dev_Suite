#!/bin/bash
# -------------------------------------------------------------
# Undeployment Script for Dev_Suite Microservices
# This script stops all the services started by deploy.sh.
#
# It uses 'pkill -f' to find processes by matching the script name.
#
# Usage:
#   chmod +x undeploy.sh
#   ./undeploy.sh
# -------------------------------------------------------------

echo "Undeploying Dev_Suite application services..."

# Define the service script names that were started by deploy.sh.
SERVICES=(
    "servicemanager.py"
    "agentregistry.py"
    "fileserver.py"
    "gitservice.py"
    "developeragent.py"
    "manageragent.py"
    "testagent.py"
    "frontend_app.py"
)

# Loop over each service and attempt to kill any matching processes.
for service in "${SERVICES[@]}"; do
    echo "Stopping processes matching: $service"
    pkill -f "$service" && echo "✔ $service stopped" || echo "⚠ No process found for $service"
done

echo "-------------------------------------------------------------"
echo "Undeployment complete. All services should be stopped."
echo "-------------------------------------------------------------"
