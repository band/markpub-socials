#!/usr/bin/env python3

import logging, os
log_level = os.environ.get('LOGLEVEL', 'WARNING').upper()

logging.basicConfig(
    level=getattr(logging, log_level, 'WARNING'),
    format="%(asctime)s - %(name)s - %(levelname)s: %(filename)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger('markpub_bluesky_posting')

import argparse
import base64
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from dotenv import load_dotenv
import json
from pathlib import Path
import re
import requests
import sys
import subprocess
from typing import Dict, List
from urllib.parse import unquote
import yaml

import markpub_bskypost.bluesky_post as bluesky_post
"""
only CLI input required is
- Bluesky and GitHub credentials if not defined in the Environment
- markpub_website and repo_name if no "bskypost.yaml" file found
"""

# create web-safe filepaths
def scrub_path(filepath):
    return re.sub(r'([ _?\#%"]+)', '_', filepath)

def format_embed_url(filepath):
    logger.debug(f"filepath: {filepath}")
    relative_url = scrub_path(Path(filepath).with_suffix('.html').as_posix())
    logger.debug(f"the relative url: {relative_url}")
    return relative_url

def get_repo_filename(webpage_url, repo_name):
    """
    Extract the repository filepath from the webpage
    Args:
        webpage_url (str): The URL of the webpage to be embedded in a Bluesky post
        repo_name (str): The name of the repository to base the relative path on
    Returns:
        str or None: filesystem path of the source Markdown file, None if an error occurs
    Raises:
        RequestsException: If the specified URL request fails
    """
    logger.debug(f"webpage_url: {webpage_url}")
    logger.debug(f"repo_name: {repo_name}")
    try:
        headers= {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        # extract the fs_path from the <meta> tag
        meta_element = soup.find('meta', {'name': 'fs_path'})
        if meta_element and meta_element.get('content'):
        #    return meta_element.get('content')
            fs_path = meta_element.get('content')
        logger.debug(f"fs_path: {fs_path}")
        if not fs_path:
            return None
        return fs_path
    except requests.RequestException as e:
        logger.error(f"Error fetching webpage: {e}")
        return None
    except Exception as e:
        logger.error(f"Error parsing webpage: {e}")
        return None

def trim_with_ellipsis(text, limit):
    """
    Trim text to a previous word boundary when it exceeds the character limit
    and add "..." at the end.
    """
    # Find the last space before the limit
    if (last_space := text[:limit].rfind(' ')) == -1:
        return text[:limit] + "..."
    # Trim to the last complete word and add ellipsis
    return text[:last_space] + "..."

def get_valid_post(char_limit):
    """
    Prompt for text for a Bluesky post, ensuring it stays within
    the character limit or offering to truncate it.
    Args:
        char_limit (int): The maximum number of characters allowed for the post
    Returns:
        str: Valid post text within the character limit
    Raises:
        SystemExit: If the user interrupts with CTRL-C
    """
    while True:
        try:
            text = input(f"Enter bluesky post text ({char_limit} characters available): ")
            if len(text) <= char_limit:
                return text

            print(f"The text is {len(text)} characters long, and exceeds the {char_limit} character limit.")
            choice = input("The options are (1) re-enter a shorter text or (2) truncate the text. Enter 1 or 2: ")
            match choice:
                case "1":
                    continue
                case "2":
                    truncated_text = trim_with_ellipsis(text, char_limit)
                    print(f"The text is truncated to: {truncated_text}")
                    return truncated_text
                case _:
                    print("Invalid option; please enter 1 or 2.")
        except KeyboardInterrupt:
            print("\nCTRL-C detected; exiting.")
            exit()

def get_markpub_url():
    try:
        markpub_url = input("Enter the Markpub webpage URL: ").strip()
        if (markpub_url.startswith('"') and markpub_url.endswith('"')) or (markpub_url.startswith("'") and markpub_url.endswith("'")):
            markpub_url = markpub_url[1:-1]
        return markpub_url
    except KeyboardInterrupt:
        print("\nCTRL-C detected; exiting.")
        exit()

def update_github_file_api(repo_name, file_path, new_content, commit_message, token=None):
    """
    Update a file in a GitHub repository by adding to its YAML frontmatter.
    If frontmatter exists, it adds to it; otherwise, it creates new frontmatter.
    Args:
        repo_name (str): Repository name in format 'username/repo'
        file_path (str): Path to the file in the repository
        new_content (str): Bluesky post URL to add to the file
        commit_message (str): Message to use for the commit
        token (str, optional): GitHub personal access token. If None, uses GH_TOKEN environment variable
    Returns:
        bool: True if successful, False otherwise
    Raises:
        ValueError: If GitHub token is not provided and not in environment variables
        requests.exceptions.RequestException: If the API request fails
    """
    try:
        # Get GitHub token
        if token is None:
            token = os.environ.get('GH_TOKEN')
            if not token:
                raise ValueError("GitHub token not provided and GH_TOKEN environment variable not set")
        
        # API URLs
        api_url = f"https://api.github.com/repos/{repo_name}/contents/{file_path}"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        # Get the file to retrieve its SHA and content if appending
        response = requests.get(api_url, headers=headers)
        response.raise_for_status()
        
        # Extract the SHA and current content if appending
        file_data = response.json()
        file_sha = file_data["sha"]
        
        # Decode the current content (it is in base64)
        current_content = base64.b64decode(file_data["content"]).decode("utf-8")
        # Insert bluesky_comments_post URL as YAML frontmatter
        # check for existing YAML frontmatter
        if current_content.startswith("---\n"):
            content_to_upload = f"---\nbluesky_comments_post: {new_content}\n" + current_content[4:]
        else:
             bsky_post_content = f"---\nbluesky_comments_post: {new_content}\n---\n"
             content_to_upload = bsky_post_content + current_content
        
        content_bytes = content_to_upload.encode("utf-8")
        base64_content = base64.b64encode(content_bytes).decode("utf-8")
        
        update_data = {
            "message": commit_message,
            "content": base64_content,
            "sha": file_sha
        }
        
        response = requests.put(api_url, headers=headers, data=json.dumps(update_data))
        response.raise_for_status()
        logger.info(f"✅ Successfully added to {file_path} in {repo_name}")
        return True
        
    except requests.exceptions.RequestException as e:
        error_msg = str(e)
        if hasattr(e, 'response') and e.response:
            try:
                error_detail = e.response.json()
                error_msg = f"{str(e)} - {json.dumps(error_detail)}"
            except:
                pass
        print(f"❌ API request failed: {error_msg}")
        return False
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False

def git_pull():
    try:
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, check=True)
        print(result.stdout)
        return 0
    except subprocess.CalledProcessError as e:
        print(f"Error: {e.stderr}")
        return 1

def main():
    # get environment settings
    load_dotenv()
    parser = argparse.ArgumentParser(description="Post MarkPub webpage to Bluesky and update Markdown page with bluesky-post URL")
    # Bluesky post arguments
    parser.add_argument(
        "--pds-url", metavar="BLUESKY_HOST", default=os.getenv("ATP_PDS_HOST") or "https://bsky.social"
    )
    parser.add_argument("--handle", metavar="BLUESKY_HANDLE", default=os.getenv("ATP_AUTH_HANDLE"))
    parser.add_argument("--password", metavar="BLUESKY_PASSWORD", default=os.getenv("ATP_AUTH_PASSWORD"))
    # GitHub API arguments
    parser.add_argument("--token", metavar="GITHUB_TOKEN", default=os.getenv("GH_TOKEN"))
    parser.add_argument("--markpubsite", default='', help="domain name where Markpub website is published")
    parser.add_argument("--reponame", default='', help="repository owner/repository-name")
    parser.add_argument('--config', '-c', default='./bskypost.yaml', help='path to YAML config file')
    
    args = parser.parse_args()
    logger.debug(f"args: {args}")

    if not (args.handle and args.password):
        logger.critical("both Bluesky handle and password are required")
        return -1
    
    if not (args.token):
        logger.critical("GitHub access token is required")
        return -1

    config = {
        'markpub_website': None,
        'repo_name': None
    }
    config_file = args.config if args.config else './bskypost.yaml'
    logger.debug(f"config file: {config_file}")
    if not Path(config_file).exists():
        # config file does not exist, look for command line arguments
        if not (args.markpubsite and args.reponame):
            logger.critical("Error: Both markpubsite and reponame must be provided either in bskypost.yaml or as command-line arguments")
            parser.print_help()
            return -1
    try:
        with open(Path(config_file), 'r') as file:
            yaml_config = yaml.safe_load(file) or {}
        if yaml_config and isinstance(config, dict):
            config['markpub_website'] = yaml_config.get('markpub_website')
            config['repo_name'] = yaml_config.get('repo_name')
    except Exception as e:
       logger.error(f"Error reading YAML file: {e}")

    # command line args overwrite config file values
    updates = {key: value for key, value in {'markpub_website': args.markpubsite, 'repo_name': args.reponame}.items() if value}
    config.update(updates)

    if (missing := [param for param, value in [("markpubsite", config.get('markpub_website')), ("reponame", config.get('repo_name'))] if value is None]):
        print(f"Error: Both markpubsite and reponame must be provided either in config file or as command-line arguments")
        print(f"Missing: {', '.join(missing)}")
        parser.print_help()
        return -1

    logger.debug(f"final configuration: markpubsite={config['markpub_website']}, reponame={config['repo_name']}")
    
    # get filename and embed_url path
    webpage_url = get_markpub_url()
    logger.debug(f"webpage url: {webpage_url}")
    repo_filename = get_repo_filename(webpage_url, config['repo_name'].split("/")[-1])
    logger.debug(f"repo_filename: {repo_filename}")
    if repo_filename:
        post_text = get_valid_post(299-len(webpage_url))
    else:
        print("MarkPub webpage URL processing failed. Exiting.")
        return -1

    setattr(args, 'embed_url', webpage_url)
    setattr(args, 'text', post_text)
    logger.debug(f"updated args: {args}")

    bsky_post_url = bluesky_post.create_post(args)
    logger.debug(f"bluesky_post return: {bsky_post_url}")
    
    if not update_github_file_api(
            repo_name=config['repo_name'],
            file_path=relative_filename,
            new_content=bsky_post_url,
            commit_message="add bluesky post URL frontmatter",
            token=args.token):
        return -1

    # synchronize local clone with remote repo
    if config.get('repo_name').split('/')[-1] in os.getcwd():
        git_pull()
    else:
        print("Do not forget to `git pull` in the local repository directory.")


if __name__ == "__main__":
    exit(main())

