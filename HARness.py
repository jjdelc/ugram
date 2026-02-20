"""
This file will leverage ugram.py but work with .har files that you can
obtain from your browser when scrolling through an Instagram profiles page.

It helps to filter the requests for "query" and save those as a .har file.

Then process them here to post them to the micropub endpoint.

"""
import argparse
import json
import os
import sys
import base64
from datetime import datetime

from ugram import IGPost, Post, MicroPubSite, str2bool


def process_nodes(nodes: list[dict], start_date, end_date) -> list[dict]:
    """
    From the obtained nodes from the HAR file, extract the values we need
    to construct the IGPost() instances we want.
    """
    found = []
    for data in nodes:
        node = data["node"]
        code = node.get('code')

        # 2. node.caption info
        # Handle cases where caption might be None
        caption_obj = node.get('caption') or {}
        if not caption_obj:
            continue

        created_at = caption_obj.get('created_at')
        captured_date = datetime.fromtimestamp(created_at).date()
        if not (start_date <= captured_date <= end_date):
            continue

        text = caption_obj.get('text')

        # 3. node.image_versions2.candidates[0].url
        # Safely access nested lists/dicts
        main_image_url = None
        img_versions = node.get('image_versions2', {})
        if img_versions and 'candidates' in img_versions:
            candidates = img_versions['candidates']
            if isinstance(candidates, list) and len(candidates) > 0:
                main_image_url = candidates[0].get('url')

        # 4. node.video_versions[0].url
        main_video_url = None
        vid_versions = node.get('video_versions')
        if isinstance(vid_versions, list) and len(vid_versions) > 0:
            main_video_url = vid_versions[0].get('url')

        # 5. Carousel media
        carousel_urls = []
        carousel_media = node.get('carousel_media')
        if isinstance(carousel_media, list):
            for item in carousel_media:
                c_img_vers = item.get('image_versions2', {})
                c_cands = c_img_vers.get('candidates', [])
                if isinstance(c_cands, list) and len(c_cands) > 0:
                    carousel_urls.append(c_cands[0].get('url'))

        # Build the small JSON
        result = {
            "code": code,
            "created_at": created_at,
            "text": text,
            "image_url": main_image_url,
            "video_url": main_video_url,
            "carousel_urls": carousel_urls,
            "is_video": bool(main_video_url),
        }
        found.append(result)

    return found


def extract_nodes_from_json(data):
    """
    Recursively searches for objects containing a 'node' key within 'edges'
    lists or just 'node' keys in general if they match the structure.
    Commonly in this data, they are inside a list under the key "edges".
    """
    found = []

    if isinstance(data, dict):
        # Check if this dictionary has a 'node' key directly (and implies it's the wrapper)
        # If we are iterating a list (like edges), the item itself is the wrapper.
        # But here we are traversing.
        if 'edges' in data and isinstance(data['edges'], list):
            for edge in data['edges']:
                if isinstance(edge, dict) and 'node' in edge:
                    found.append(edge)

        # Continue recursive search in all values
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                found.extend(extract_nodes_from_json(value))

    elif isinstance(data, list):
        for item in data:
            found.extend(extract_nodes_from_json(item))

    return found


def process_har_file(har_data: dict) -> list[dict]:
    """
    Given a HAR file, find the nodes inside the bodies of those responses
    that contain the information from the IG posts.
    """
    result_nodes = []
    entries = har_data['log']['entries']

    for entry in entries:
        response = entry.get('response', {})
        content = response.get('content', {})
        mime_type = content.get('mimeType', '')
        text = content.get('text', '')

        if not text:
            continue

        # Handle base64 encoding if present (though rare for JSON text in HAR)
        if content.get('encoding') == 'base64':
            try:
                text = base64.b64decode(text).decode('utf-8', errors='ignore')
            except Exception:
                continue

        # We are looking for JSON responses
        if 'json' in mime_type or (
                text.strip().startswith('{') and text.strip().endswith('}')):
            try:
                json_body = json.loads(text)

                # Extract matching nodes
                nodes = extract_nodes_from_json(json_body)
                result_nodes.extend(nodes)

            except json.JSONDecodeError:
                continue

    return result_nodes


def parse_date(date_str):
    """Validates date format (YYYY/MM/DD)."""
    try:
        return datetime.strptime(date_str, "%Y/%m/%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid date: '{date_str}'. Expected: YYYY/MM/DD")


def main():
    parser = argparse.ArgumentParser(
        description="Process HAR file with config and date range.")

    parser.add_argument('config_file', help="Path to the config.json file")
    parser.add_argument('har_file', help="Path to the input .har file")

    parser.add_argument('--from', dest='start_date', type=parse_date,
                        required=True)
    parser.add_argument('--to', dest='end_date', type=parse_date, required=True)
    parser.add_argument('--commit', type=str2bool, nargs='?', const=True,
                        default=False)
    parser.add_argument('--syndicate', type=str2bool, nargs='?', const=True,
                        default=False)

    args = parser.parse_args()

    # --- Validations ---
    json_contents = []
    for f in [args.config_file, args.har_file]:
        if not os.path.exists(f):
            sys.exit(f"❌ Error: File not found: {f}")

        try:
            with open(f, 'r', encoding='utf-8') as opened_f:
                json_contents.append(json.load(opened_f))
        except json.JSONDecodeError:
            print(f"Error: Failed to parse '{f}' as JSON.")
            sys.exit(1)

    if not args.har_file.lower().endswith('.har'):
        sys.exit(
            f"Error: The input file '{args.har_file}' must be a .har file.")

    if args.start_date >= args.end_date:
        sys.exit(f"Error: Start date must be before end date.")

    # Actual main execution
    config, har_contents = json_contents
    mp_endpoint = config["endpoint"]
    token = config["token"]
    site = MicroPubSite(mp_endpoint, token)

    nodes = process_har_file(har_contents)
    filtered_nodes = process_nodes(nodes, args.start_date, args.end_date)
    ig_posts = [IGPost.from_filtered_node(n) for n in filtered_nodes]
    posts = [Post(ig, ig.publish_date) for ig in ig_posts]

    for post in reversed(posts):  # Post older ones first
        if args.commit:
            post.post(site, args.syndicate)
        else:
            post.print(site, args.syndicate)


if __name__ == "__main__":
    """
    HAR Data Processor

    This script parses a HTTP Archive (.har) file and filters network entries 
    based on a specific date range. Validated entries can then be committed 
    to a destination defined in the configuration file.

    Usage:
        python file.py config.json input.har --from=2023/01/01 --to=2023/12/31 [--commit] [--syndicate]

    Arguments:
        config_file (str): Path to a JSON configuration file containing 
                           credentials or mapping logic.
        har_file (str):    Path to the .har file to be processed.

    Flags:
        --from:   The start date (inclusive) in YYYY/MM/DD format.
        --to:     The end date (inclusive) in YYYY/MM/DD format.
        --commit: Optional flag. If present, changes will be written to the 
                  destination. Defaults to False (Dry Run mode).

    Workflow:
        1. Validates file existence and date logic.
        2. Parses the Unix Epoch timestamps within the HAR entries.
        3. Filters entries where: start_date <= entry_date <= end_date.
        4. Executes logic based on the --commit status.
    """
    main()
