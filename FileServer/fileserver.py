from flask import Flask, request, jsonify
import os
import uuid
import glob
import difflib
import shutil
import datetime
from dotenv import load_dotenv

app = Flask(__name__)
load_dotenv()

upload_path = os.getenv("UPLOAD_PATH", None)

# Configuration: Set a base upload directory and allowed directories.
BASE_DIR = os.path.abspath(upload_path)
ALLOWED_DIRS = [BASE_DIR]  # You can add more directories to this list.

# Ensure BASE_DIR exists.
if not os.path.exists(BASE_DIR):
    os.makedirs(BASE_DIR)


def is_path_allowed(path: str) -> bool:
    """
    Check if the given absolute path is within one of the allowed directories.
    """
    abs_path = os.path.abspath(path)
    for allowed in ALLOWED_DIRS:
        allowed_abs = os.path.abspath(allowed)
        if os.path.commonpath([abs_path, allowed_abs]) == allowed_abs:
            return True
    return False


def get_full_path(path: str) -> str:
    """
    Convert a given path to an absolute path relative to BASE_DIR
    if it is not already absolute.
    """
    if os.path.isabs(path):
        return os.path.abspath(path)
    return os.path.abspath(os.path.join(BASE_DIR, path))


@app.route('/read_file', methods=['GET'])
def read_file():
    """
    Read complete contents of a file.
    Query Parameter: 
      - path (string): relative (to BASE_DIR) or absolute path to the file.
    """
    path = request.args.get('path')
    if not path:
        return jsonify({'error': 'Path parameter is required.'}), 400

    full_path = get_full_path(path)
    if not is_path_allowed(full_path):
        return jsonify({'error': 'Access to this path is not allowed.'}), 403

    try:
        if not os.path.exists(full_path):
            # Return a 404 with a message rather than a 500 error
            return jsonify({
                'error': 'File not found',
                'file_path': path
            }), 404
        
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
        return jsonify({'content': content}), 200
    except Exception as e:
        # Log the error for debugging
        app.logger.error(f"Error reading file {path}: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/read_multiple_files', methods=['POST'])
def read_multiple_files():
    """
    Read multiple files simultaneously.
    Expected JSON Input:
      { "paths": ["path1", "path2", ...] }
    Failed reads won't stop the entire operation.
    """
    data = request.get_json()
    if not data or "paths" not in data:
        return jsonify({'error': 'JSON payload with "paths" required.'}), 400

    results = {}
    for path in data["paths"]:
        full_path = get_full_path(path)
        if not is_path_allowed(full_path):
            results[path] = {"error": "Access not allowed."}
            continue
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                results[path] = {"content": f.read()}
        except Exception as e:
            results[path] = {"error": str(e)}
    return jsonify(results), 200


@app.route('/write_file', methods=['POST'])
def write_file():
    """
    Create a new file or overwrite an existing one.
    Expected JSON Input:
      { "path": "file_path", "content": "File content" }
    """
    data = request.get_json()
    if not data or "path" not in data or "content" not in data:
        return jsonify({'error': 'JSON payload with "path" and "content" required.'}), 400

    path = data["path"]
    content = data["content"]

    full_path = get_full_path(path)
    if not is_path_allowed(full_path):
        return jsonify({'error': 'Access to this path is not allowed.'}), 403

    try:
        # Ensure parent directories exist.
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return jsonify({'message': 'File written successfully.'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/edit_file', methods=['POST'])
def edit_file():
    """
    Edit file content with advanced options.
    Expected JSON Input:
      {
          "path": "file_path",
          "edits": [
              {
                "oldText": "text to find",
                "newText": "text to replace"
              },
              ... // multiple edit operations allowed
          ],
          "dryRun": <boolean>, // optional, default false
          "options": {
              "preserveIndentation": <boolean>,
              "normalizeWhitespace": <boolean>,
              "partialMatch": <boolean>
          }
      }
    Returns a git-style diff if dryRun is true, or applies changes otherwise.
    """
    data = request.get_json()
    required_keys = ["path", "edits"]
    if not data or not all(key in data for key in required_keys):
        return jsonify({'error': 'JSON payload with "path" and "edits" required.'}), 400

    path = data["path"]
    edits = data["edits"]
    dry_run = data.get("dryRun", False)
    options = data.get("options", {})
    preserve_indentation = options.get("preserveIndentation", True)
    normalize_whitespace = options.get("normalizeWhitespace", True)
    partial_match = options.get("partialMatch", True)

    full_path = get_full_path(path)
    if not is_path_allowed(full_path):
        return jsonify({'error': 'Access to this path is not allowed.'}), 403

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            original_content = f.read()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    new_content = original_content

    # For each edit, perform a replacement.
    for edit in edits:
        old_text = edit.get("oldText", "")
        new_text = edit.get("newText", "")
        if not old_text:
            continue

        # Simple substring replacement (partialMatch is enabled by default).
        new_content = new_content.replace(old_text, new_text)

    # Normalize whitespace if needed.
    if normalize_whitespace:
        new_content = "\n".join(line.strip() for line in new_content.splitlines())

    if dry_run:
        # Generate a git-style diff using difflib.
        diff = difflib.unified_diff(
            original_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile='original',
            tofile='modified'
        )
        diff_text = "".join(diff)
        return jsonify({
            'dryRun': True,
            'diff': diff_text,
            'message': 'Preview of changes. No changes applied.'
        }), 200
    else:
        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            return jsonify({'message': 'File edited successfully.'}), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500


@app.route('/create_directory', methods=['POST'])
def create_directory():
    """
    Create a new directory or ensure it exists.
    Expected JSON Input:
      { "path": "directory_path" }
    """
    data = request.get_json()
    if not data or "path" not in data:
        return jsonify({'error': 'JSON payload with "path" required.'}), 400

    path = data["path"]
    full_path = get_full_path(path)
    if not is_path_allowed(full_path):
        return jsonify({'error': 'Access to this path is not allowed.'}), 403

    try:
        os.makedirs(full_path, exist_ok=True)
        return jsonify({'message': 'Directory created or already exists.'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/list_directory', methods=['GET'])
def list_directory():
    """
    List directory contents with [FILE] or [DIR] prefixes.
    Query Parameter:
      - path (string): directory path to list (relative to BASE_DIR; default is BASE_DIR).
    """
    # If no path is provided, use BASE_DIR.
    rel_path = request.args.get("path", "")
    full_path = get_full_path(rel_path) if rel_path else BASE_DIR

    if not is_path_allowed(full_path):
        return jsonify({'error': 'Access to this path is not allowed.'}), 403

    if not os.path.isdir(full_path):
        return jsonify({'error': 'Specified path is not a directory.'}), 400

    try:
        items = os.listdir(full_path)
        results = []
        for item in items:
            full_item = os.path.join(full_path, item)
            if os.path.isdir(full_item):
                results.append(f"[DIR] {item}")
            else:
                results.append(f"[FILE] {item}")
        return jsonify({'directory': full_path, 'contents': results}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/move_file', methods=['POST'])
def move_file():
    """
    Move or rename files and directories.
    Expected JSON Input:
      { "source": "source_path", "destination": "destination_path" }
    Fails if the destination exists.
    """
    data = request.get_json()
    if not data or "source" not in data or "destination" not in data:
        return jsonify({'error': 'JSON payload with "source" and "destination" required.'}), 400

    source = data["source"]
    destination = data["destination"]

    source_full = get_full_path(source)
    destination_full = get_full_path(destination)

    if not is_path_allowed(source_full) or not is_path_allowed(destination_full):
        return jsonify({'error': 'Access to source or destination is not allowed.'}), 403

    if not os.path.exists(source_full):
        return jsonify({'error': 'Source file/directory does not exist.'}), 404

    if os.path.exists(destination_full):
        return jsonify({'error': 'Destination already exists.'}), 400

    try:
        # Ensure the destination directory exists.
        os.makedirs(os.path.dirname(destination_full), exist_ok=True)
        shutil.move(source_full, destination_full)
        return jsonify({'message': 'File/directory moved successfully.'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/search_files', methods=['GET'])
def search_files():
    """
    Recursively search for files/directories.
    Query Parameters:
      - path (string): Starting directory (relative to BASE_DIR; default is BASE_DIR).
      - pattern (string): Search pattern.
      - excludePatterns (string): Comma-separated list of patterns to exclude.
    Case-insensitive matching is applied.
    Returns full paths to matches.
    """
    rel_path = request.args.get("path", "")
    full_start_path = get_full_path(rel_path) if rel_path else BASE_DIR
    pattern = request.args.get("pattern", "*")
    exclude_patterns = request.args.get("excludePatterns", "")
    exclude_list = [p.strip() for p in exclude_patterns.split(",")] if exclude_patterns else []

    if not is_path_allowed(full_start_path):
        return jsonify({'error': 'Access to this path is not allowed.'}), 403

    matches = []
    try:
        for root, dirs, files in os.walk(full_start_path):
            for name in files + dirs:
                # Case-insensitive match.
                if glob.fnmatch.fnmatch(name.lower(), pattern.lower()):
                    full_path = os.path.join(root, name)
                    # Exclude any matching patterns.
                    if any(glob.fnmatch.fnmatch(name, pat) for pat in exclude_list):
                        continue
                    matches.append(full_path)
        return jsonify({'matches': matches}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/get_file_info', methods=['GET'])
def get_file_info():
    """
    Get detailed file/directory metadata.
    Query Parameter:
      - path (string): The file or directory to inspect (relative to BASE_DIR).
    Returns: Size, creation time, modified time, access time, type, and permissions.
    """
    path = request.args.get("path")
    if not path:
        return jsonify({'error': 'Path parameter is required.'}), 400

    full_path = get_full_path(path)
    if not is_path_allowed(full_path):
        return jsonify({'error': 'Access to this path is not allowed.'}), 403

    if not os.path.exists(full_path):
        return jsonify({'error': 'Path does not exist.'}), 404

    try:
        stat_info = os.stat(full_path)
        file_info = {
            "size": stat_info.st_size,
            "creation_time": datetime.datetime.fromtimestamp(stat_info.st_ctime).isoformat(),
            "modified_time": datetime.datetime.fromtimestamp(stat_info.st_mtime).isoformat(),
            "access_time": datetime.datetime.fromtimestamp(stat_info.st_atime).isoformat(),
            "type": "directory" if os.path.isdir(full_path) else "file",
            "permissions": oct(stat_info.st_mode)[-3:]
        }
        return jsonify({'file_info': file_info}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/list_allowed_directories', methods=['GET'])
def list_allowed_directories():
    """
    List all directories the server is allowed to access.
    No input required.
    Returns the list of allowed directories.
    """
    return jsonify({'allowed_directories': ALLOWED_DIRS}), 200


# Error handlers for common error codes.
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def server_error(error):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    app.run(debug=True, port=6000)