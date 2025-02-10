# test_agent_registry.py
import time
import pytest
from agentregistry import app, registry  # Adjust the module name if necessary


@pytest.fixture
def client():
    """Create a Flask test client and clear the registry before each test."""
    app.config['TESTING'] = True
    with app.test_client() as client:
        # Clear the in-memory registry to ensure tests are isolated.
        with registry._lock:
            registry._registry.clear()
        yield client


def test_register_success(client):
    payload = {
        "agent_name": "TestAgent",
        "capabilities": ["cap1", "cap2"]
    }
    response = client.post('/register', json=payload)
    assert response.status_code == 200, response.get_data(as_text=True)
    data = response.get_json()
    assert "message" in data
    assert "TestAgent" in data["message"]


def test_register_missing_fields(client):
    # Test missing 'capabilities'
    payload = {"agent_name": "TestAgent"}
    response = client.post('/register', json=payload)
    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data


def test_heartbeat_success(client):
    # First, register the agent.
    client.post('/register', json={"agent_name": "TestAgent", "capabilities": ["cap1"]})
    
    # Send a heartbeat.
    response = client.post('/heartbeat', json={"agent_name": "TestAgent"})
    assert response.status_code == 200, response.get_data(as_text=True)
    data = response.get_json()
    assert "message" in data


def test_heartbeat_missing_agent(client):
    # Missing agent_name in heartbeat payload.
    response = client.post('/heartbeat', json={})
    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data


def test_unregister_success(client):
    # Register then unregister an agent.
    client.post('/register', json={"agent_name": "TestAgent", "capabilities": ["cap1"]})
    response = client.post('/unregister', json={"agent_name": "TestAgent"})
    assert response.status_code == 200
    data = response.get_json()
    assert "message" in data

    # Verify the agent has been unregistered.
    response = client.get('/get_capabilities/TestAgent')
    assert response.status_code == 404


def test_get_capabilities_success(client):
    # Register an agent then retrieve its capabilities.
    client.post('/register', json={"agent_name": "TestAgent", "capabilities": ["cap1", "cap2"]})
    response = client.get('/get_capabilities/TestAgent')
    assert response.status_code == 200, response.get_data(as_text=True)
    data = response.get_json()
    assert data.get("agent_name") == "TestAgent"
    assert data.get("capabilities") == ["cap1", "cap2"]


def test_get_capabilities_not_found(client):
    # Try to get capabilities for a non-existent agent.
    response = client.get('/get_capabilities/NonExistentAgent')
    assert response.status_code == 404
    data = response.get_json()
    assert "error" in data


def test_list_agents(client):
    # Register two agents then list them.
    client.post('/register', json={"agent_name": "Agent1", "capabilities": ["a", "b"]})
    client.post('/register', json={"agent_name": "Agent2", "capabilities": ["c", "d"]})
    response = client.get('/list_agents')
    assert response.status_code == 200
    data = response.get_json()
    assert "Agent1" in data
    assert "Agent2" in data
    assert data["Agent1"] == ["a", "b"]
    assert data["Agent2"] == ["c", "d"]


def test_check_agent_health(client):
    # Register an agent and then simulate staleness by modifying its last heartbeat.
    client.post('/register', json={"agent_name": "StaleAgent", "capabilities": ["old"]})
    
    # Simulate a stale agent (e.g., last heartbeat 200 seconds ago).
    with registry._lock:
        registry._registry["StaleAgent"]["last_heartbeat"] = time.time() - 200

    # Check health with a timeout of 60 seconds.
    response = client.get('/check_agent_health?timeout=60')
    assert response.status_code == 200, response.get_data(as_text=True)
    data = response.get_json()
    assert "unhealthy_agents" in data
    assert "StaleAgent" in data["unhealthy_agents"]
