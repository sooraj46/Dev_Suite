import os
import requests
from urllib.parse import urljoin

# --- Configuration ---
# Base URL for the file server (adjust host and port as needed)
FILE_SERVER_BASE_URL = "http://localhost:5000/"
# Base path on the file server where the repo will be stored.
# This path must be under one of the allowed directories configured in the file server.
FILE_SERVER_REPO_BASE = "uploads/repo"

# --- Utility Functions ---

def file_server_write_file(path: str, content: str) -> bool:
    """
    Calls the file server /write_file endpoint to create or overwrite a file.
    :param path: Full file path on the server (relative to allowed directory)
    :param content: File content as a string.
    :return: True if the write succeeded; otherwise, False.
    """
    url = urljoin(FILE_SERVER_BASE_URL, "write_file")
    payload = {"path": path, "content": content}
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        print(f"Uploaded: {path}")
        return True
    except Exception as e:
        print(f"Error uploading {path}: {e}")
        return False

def file_server_list_directory(path: str) -> list:
    """
    Calls the file server /list_directory endpoint to list the contents of a directory.
    :param path: Directory path on the server.
    :return: A list of items (strings) returned by the file server.
    """
    url = urljoin(FILE_SERVER_BASE_URL, "list_directory")
    params = {"path": path}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        return data.get("contents", [])
    except Exception as e:
        print(f"Error listing directory {path}: {e}")
        return []

def file_server_download_file(path: str, filename: str, local_path: str) -> bool:
    """
    Downloads a file from the file server using the /download endpoint.
    :param path: The subdirectory (on the server) where the file is located.
    :param filename: Name of the file to download.
    :param local_path: Local path where the file should be saved.
    :return: True if the download succeeded; otherwise, False.
    """
    url = urljoin(FILE_SERVER_BASE_URL, "download")
    params = {"path": path, "filename": filename}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        # Save the file locally.
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(response.content)
        print(f"Downloaded: {local_path}")
        return True
    except Exception as e:
        print(f"Error downloading {filename} from {path}: {e}")
        return False

# --- Core Functions ---

def upload_repo(local_repo_path: str, server_repo_base: str = FILE_SERVER_REPO_BASE):
    """
    Upload all files from a local repository to the file server.
    The directory structure is preserved.
    :param local_repo_path: Path to the local repository root.
    :param server_repo_base: Base path on the file server where the repo is stored.
    """
    for root, dirs, files in os.walk(local_repo_path):
        for file in files:
            local_file = os.path.join(root, file)
            # Compute relative path of the file with respect to the repo root.
            rel_path = os.path.relpath(local_file, local_repo_path)
            # Create the target file path on the server.
            # This assumes that the file server accepts a full path (e.g., "uploads/repo/rel_path")
            server_file_path = os.path.join(server_repo_base, rel_path)
            try:
                # Read the file (assuming UTF-8 encoded source code)
                with open(local_file, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                print(f"Skipping {local_file} (could not read): {e}")
                continue

            file_server_write_file(server_file_path, content)

def download_repo(local_target_path: str, server_repo_base: str = FILE_SERVER_REPO_BASE):
    """
    Download the entire repository from the file server to a local directory.
    Recursively walks the file server directory structure using the /list_directory endpoint,
    downloads each file, and writes it locally while preserving structure.
    :param local_target_path: Local path where the repo will be recreated.
    :param server_repo_base: Base path on the file server where the repo is stored.
    """
    def _download_recursive(server_path: str, local_path: str):
        # List contents in the server directory.
        items = file_server_list_directory(server_path)
        for item in items:
            # The file server returns items in the format "[DIR] foldername" or "[FILE] filename"
            if item.startswith("[DIR]"):
                dirname = item.replace("[DIR] ", "").strip()
                new_server_path = os.path.join(server_path, dirname)
                new_local_path = os.path.join(local_path, dirname)
                os.makedirs(new_local_path, exist_ok=True)
                _download_recursive(new_server_path, new_local_path)
            elif item.startswith("[FILE]"):
                filename = item.replace("[FILE] ", "").strip()
                local_file = os.path.join(local_path, filename)
                file_server_download_file(server_path, filename, local_file)
            else:
                print(f"Unknown item type: {item}")

    os.makedirs(local_target_path, exist_ok=True)
    _download_recursive(server_repo_base, local_target_path)

# --- Example Usage ---

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Upload/Download repo to/from file server")
    parser.add_argument("--upload", action="store_true", help="Upload repository to file server")
    parser.add_argument("--download", action="store_true", help="Download repository from file server")
    parser.add_argument("--local", type=str, required=True, help="Local repository path (for upload) or target path (for download)")
    args = parser.parse_args()

    if args.upload:
        print("Uploading repository...")
        upload_repo(args.local)
    elif args.download:
        print("Downloading repository...")
        download_repo(args.local)
    else:
        print("Please specify --upload or --download.")

