import os
import time
import threading
from flask import Flask, request, jsonify


# --- Capability Registry Class ---

class CapabilityRegistry:
    def __init__(self):
        # _registry maps agent_name -> {"capabilities": [...], "last_heartbeat": <timestamp>}
        self._registry = {}
        self._lock = threading.Lock()

    def register(self, agent_name, capabilities):
        """Register or update the capabilities of an agent."""
        with self._lock:
            self._registry[agent_name] = {
                'capabilities': capabilities,
                'last_heartbeat': time.time()
            }

    def heartbeat(self, agent_name):
        """Agents call this periodically to indicate liveness."""
        with self._lock:
            if agent_name in self._registry:
                self._registry[agent_name]['last_heartbeat'] = time.time()

    def unregister(self, agent_name):
        """Unregister an agent from the in-memory store."""
        with self._lock:
            if agent_name in self._registry:
                del self._registry[agent_name]

    def get_capabilities(self, agent_name):
        """Retrieve the list of capabilities for a specific agent."""
        with self._lock:
            agent_info = self._registry.get(agent_name)
            return agent_info['capabilities'] if agent_info else []

    def list_agents(self):
        """List all registered agents with their capabilities (no timestamps)."""
        with self._lock:
            return {k: v['capabilities'] for k, v in self._registry.items()}

    def check_agent_health(self, timeout=60):
        """
        Return a list of agents whose last heartbeat is older than 'timeout' seconds.
        """
        with self._lock:
            now = time.time()
            unhealthy = []
            for agent_name, data in self._registry.items():
                if now - data['last_heartbeat'] > timeout:
                    unhealthy.append(agent_name)
            return unhealthy


# Global registry instance
registry = CapabilityRegistry()

# --- Flask Application Setup ---

app = Flask(__name__)

@app.route('/register', methods=['POST'])
def register():
    """
    Registers an agent with its capabilities.
    Expected JSON payload:
    {
        "agent_name": "Agent1",
        "capabilities": ["capability1", "capability2", ...]
    }
    """
    data = request.get_json(force=True)
    agent_name = data.get("agent_name")
    capabilities = data.get("capabilities")
    if not agent_name or not capabilities:
        return jsonify({"error": "agent_name and capabilities are required"}), 400

    registry.register(agent_name, capabilities)
    return jsonify({
        "message": f"Agent '{agent_name}' registered with capabilities: {capabilities}"
    }), 200

@app.route('/heartbeat', methods=['POST'])
def heartbeat():
    """
    Endpoint for agents to send a heartbeat.
    Expected JSON payload:
    {
        "agent_name": "Agent1"
    }
    """
    data = request.get_json(force=True)
    agent_name = data.get("agent_name")
    if not agent_name:
        return jsonify({"error": "agent_name is required"}), 400

    registry.heartbeat(agent_name)
    return jsonify({"message": f"Heartbeat received for agent '{agent_name}'"}), 200

@app.route('/unregister', methods=['POST'])
def unregister():
    """
    Unregister an agent.
    Expected JSON payload:
    {
        "agent_name": "Agent1"
    }
    """
    data = request.get_json(force=True)
    agent_name = data.get("agent_name")
    if not agent_name:
        return jsonify({"error": "agent_name is required"}), 400

    registry.unregister(agent_name)
    return jsonify({"message": f"Agent '{agent_name}' unregistered"}), 200

@app.route('/get_capabilities/<agent_name>', methods=['GET'])
def get_capabilities(agent_name):
    """
    Get the list of capabilities for a specific agent.
    """
    capabilities = registry.get_capabilities(agent_name)
    if not capabilities:
        return jsonify({"error": f"Agent '{agent_name}' not found"}), 404
    return jsonify({"agent_name": agent_name, "capabilities": capabilities}), 200

@app.route('/list_agents', methods=['GET'])
def list_agents():
    """
    List all registered agents with their capabilities.
    """
    agents = registry.list_agents()
    print(jsonify(agents))
    return jsonify(agents), 200

@app.route('/check_agent_health', methods=['GET'])
def check_agent_health():
    """
    Check for agents that have not sent a heartbeat within a specified timeout.
    Optional query parameter:
    - timeout (in seconds, default 60)
    """
    timeout = request.args.get('timeout', default=60, type=float)
    unhealthy = registry.check_agent_health(timeout)
    return jsonify({"unhealthy_agents": unhealthy}), 200


# --- Background Thread for Automatic Deregistration ---

def auto_deregister_stale_agents(interval=60, timeout=120):
    """
    Periodically checks for stale agents that haven't heartbeated within 'timeout' seconds
    and unregisters them automatically.
    """
    while True:
        time.sleep(interval)
        try:
            stale_agents = registry.check_agent_health(timeout)
            for agent in stale_agents:
                registry.unregister(agent)
                app.logger.info(f"Automatically unregistered stale agent: {agent}")
        except Exception as e:
            # Log the exception but continue looping
            app.logger.error(f"Error in auto_deregister_stale_agents: {e}")


if __name__ == '__main__':
    # Start a background thread to clean up stale agents
    cleanup_thread = threading.Thread(
        target=auto_deregister_stale_agents,
        args=(60, 120),  # check every 60s, timeout for agents is 120s
        daemon=True
    )
    cleanup_thread.start()

    # For production, consider:
    #   gunicorn agentregistry:app -b 0.0.0.0:5005
    app.run(host='0.0.0.0', port=5005)
