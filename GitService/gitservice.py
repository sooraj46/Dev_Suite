import os
import tempfile
from flask import Flask, request, jsonify, abort
from git import Repo, GitCommandError

app = Flask(__name__)
GIT_BASE_DIR = '/Users/soorajjayasundaram/upload/uploads'  # Ensure this folder is created and writable

def get_repo_path(repo_name):
    return os.path.join(GIT_BASE_DIR, repo_name)

@app.route('/init', methods=['POST'])
def init_repo():
    data = request.json
    repo_name = data.get('repo_name')
    if not repo_name:
        abort(400, 'Repository name required')
    repo_path = get_repo_path(repo_name)
    try:
        os.makedirs(repo_path, exist_ok=True)
        Repo.init(repo_path, bare=False)
        return jsonify({'message': f'Initialized repository {repo_name}', 'repo_path': repo_path})
    except Exception as e:
        abort(500, str(e))

@app.route('/commit', methods=['POST'])
def commit_changes():
    data = request.json
    repo_name = data.get('repo_name')
    commit_message = data.get('commit_message', 'Commit via API')
    file_changes = data.get('file_changes', {})  # Expected as { "filepath": "content", ... }
    if not repo_name:
        abort(400, 'Repository name required')
    repo_path = get_repo_path(repo_name)
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Repo.clone_from(repo_path, tmpdir)
            for filepath, content in file_changes.items():
                full_path = os.path.join(tmpdir, filepath)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                repo.index.add([full_path])
            commit = repo.index.commit(commit_message)
            origin = repo.remotes.origin if repo.remotes else repo.create_remote('origin', repo_path)
            origin.push()
            return jsonify({'message': 'Commit successful', 'commit': commit.hexsha})
    except GitCommandError as e:
        abort(500, f'Git error: {str(e)}')
    except Exception as e:
        abort(500, str(e))

@app.route('/merge', methods=['POST'])
def merge_branches():
    data = request.json
    repo_name = data.get('repo_name')
    source_branch = data.get('source_branch')
    target_branch = data.get('target_branch')
    if not repo_name or not source_branch or not target_branch:
        abort(400, 'Missing parameters')
    repo_path = get_repo_path(repo_name)
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Repo.clone_from(repo_path, tmpdir)
            repo.git.checkout(target_branch)
            repo.git.merge(source_branch)
            commit = repo.head.commit.hexsha
            origin = repo.remotes.origin if repo.remotes else repo.create_remote('origin', repo_path)
            origin.push()
            return jsonify({'message': 'Merge successful', 'commit': commit})
    except Exception as e:
        abort(500, str(e))

@app.route('/checkout', methods=['GET'])
def checkout_revision():
    repo_name = request.args.get('repo_name')
    revision = request.args.get('revision')
    if not repo_name or not revision:
        abort(400, 'Missing parameters')
    repo_path = get_repo_path(repo_name)
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Repo.clone_from(repo_path, tmpdir)
            repo.git.checkout(revision)
            file_tree = []
            for root, dirs, files in os.walk(tmpdir):
                for file in files:
                    rel_path = os.path.relpath(os.path.join(root, file), tmpdir)
                    file_tree.append(rel_path)
            return jsonify({'repo_name': repo_name, 'revision': revision, 'files': file_tree})
    except Exception as e:
        abort(500, str(e))

@app.route('/log', methods=['GET'])
def repo_log():
    repo_name = request.args.get('repo_name')
    branch = request.args.get('branch', 'master')
    if not repo_name:
        abort(400, 'Repository name required')
    repo_path = get_repo_path(repo_name)
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Repo.clone_from(repo_path, tmpdir)
            logs = []
            for commit in repo.iter_commits(branch):
                logs.append({
                    'hexsha': commit.hexsha,
                    'message': commit.message,
                    'author': commit.author.name,
                    'date': commit.committed_datetime.isoformat()
                })
            return jsonify({'repo_name': repo_name, 'branch': branch, 'logs': logs})
    except Exception as e:
        abort(500, str(e))

if __name__ == '__main__':
    os.makedirs(GIT_BASE_DIR, exist_ok=True)
    app.run(host='0.0.0.0', port=5001)

