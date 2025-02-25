# Dev Suite - AI-Powered Software Development Framework

Dev Suite is a distributed microservices framework that uses AI agents to automate the software development process, from requirements gathering to code generation, testing, and deployment.

## System Architecture

The system consists of several microservices that work together:

1. **ServiceManager**: Coordinates all services, handles startup, monitors health, and manages project continuity
2. **AgentRegistry**: Maintains a registry of available agents and their capabilities
3. **FileServer**: Provides file storage and retrieval for project artifacts
4. **GitService**: Handles Git operations for version control
5. **ManagerAgent**: Orchestrates the development workflow, making decisions on task assignment
6. **DeveloperAgent**: Generates code based on requirements using AI
7. **TestingAgent**: Automatically generates and runs tests for the codebase
8. **FrontendService**: Provides a web interface for users to interact with the system

## Key Features

- **Multi-project management**: Track and manage multiple software projects simultaneously
- **AI-driven development**: Automated code generation based on natural language requirements
- **Continuous testing**: Automated test generation and execution
- **Version control integration**: Git integration for source control
- **Service resilience**: Automatic recovery of services after failures
- **Project continuity**: Resume projects from their last state after system restarts
- **Interactive UI**: Web-based interface to monitor project progress and provide feedback

## Installation

### Prerequisites

- Python 3.9 or higher
- RabbitMQ (for message queuing)
- Google API Key (for AI-powered code generation)

### Setup

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/Dev_Suite.git
   cd Dev_Suite
   ```

2. Set up a virtual environment:
   ```
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

4. Create a `.env` file in the root directory with your configuration:
   ```
   GOOGLE_API_KEY=your_api_key_here
   MESSAGE_QUEUE_HOST=localhost
   FILE_SERVER_BASE_URL=http://localhost:6000
   REGISTRY_URL=http://localhost:5005
   GIT_SERVICE_URL=http://localhost:5001
   FRONTEND_SERVICE_URL=http://localhost:8080
   ```

## Usage

### Starting the System

Run the deployment script to start all services:

```
chmod +x deploy.sh
./deploy.sh
```

This will start the ServiceManager, which will handle starting all other services.

### Accessing the Web Interface

Open your browser and go to http://localhost:8080 to access the web interface.

### Submitting a New Project

1. Navigate to the "Requirements" tab
2. Fill in the requirement description and set priority
3. Click "Submit Requirement"

### Managing Projects

1. Navigate to the "Projects" tab to see all projects
2. Click on a project to view its details, status, development logs, and test results
3. Use the activity tab to see a chronological log of all actions across projects

### Stopping the System

To stop all services, run:

```
chmod +x undeploy.sh
./undeploy.sh
```

## Architecture Details

### Message-Based Communication

Services communicate through RabbitMQ using a standardized message format:

```json
{
  "message_id": "unique_id",
  "sender": "ServiceName",
  "receiver": "ServiceName",
  "timestamp": "ISO-8601 timestamp",
  "type": "MESSAGE_TYPE",
  "payload": {}
}
```

### Project Structure

Each project is stored with the following structure:

```
uploads/project_name/
├── requirements.md       # Original requirements
├── status.md             # Current project status
├── developmentstatus.md  # Detailed development logs
├── test_results.md       # Test execution results
└── [generated code files]
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.