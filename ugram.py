import sys
import json
import logging as log
from os.path import basename
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.parse import urlencode, urlparse


PROFILE = "https://www.instagram.com/{}"
DETAIL_URL = "https://www.instagram.com/p/{}/"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:71.0) Gecko/20100101 Firefox/71.0"

log.basicConfig(level=log.DEBUG)


def fetch_profile(profile_url):
    log.debug("Reading Instagram profile: {}".format(profile_url))
    req = Request(profile_url, headers={
        "User-Agent": UA
    })
    profile_html = urlopen(req).read().decode("utf-8")
    return profile_html


def parse(profile_html):
    """
    Given the bare HTML from GETting to an IG user's profile, extract only
    the JSON payload that contains the user's information.

    :param profile_html: raw HTML from user's profile
    :return: Dict with user's data
    """
    scripts, partial = [], []
    for line in profile_html.split("\n"):
        if "<script " in line:
            partial.append(line)
        elif "</script>" in line:
            scripts_text = " ".join(partial)
            nodes = scripts_text.split("</script>")
            scripts.extend(nodes)
            partial = []

    content_node = [s for s in scripts if "biography" in s][0]
    # Matches script that starts with `window._sharedData = ....`
    json_content = content_node.split(" = ")[1].replace("</script>", "").rstrip(";")
    payload = json.loads(json_content)
    return payload


def extract_pictures(payload):
    """
    :param payload: This dict should be extracted from the HTML of the user
        profile, and contains the whole user's data. We'll extract only the
        pictures from this.
    :return: List of dictionaries for the pictures found in the JSON payload
    """
    user_payload = payload["entry_data"]["ProfilePage"][0]["graphql"]["user"]
    pictures = user_payload["edge_owner_to_timeline_media"]["edges"]
    log.debug("Found {} pictures".format(len(pictures)))
    return pictures


class IGPic:
    """
    A wrapper around the IG bare HTML JSON structure, in order to strip out
    the attributes we care for.
    """
    def __init__(self, node):
        node = node["node"]
        self.code = node["shortcode"]
        self.thumb = node["thumbnail_src"]
        self.dims = node["dimensions"]
        self.picture_url = node["display_url"]
        self.timestamp = node["taken_at_timestamp"]
        self.text = node["edge_media_to_caption"]["edges"][0]["node"]["text"]


class Post:
    def __init__(self, pic, endpoint, token):
        self.pic = pic
        self.endpoint = endpoint
        self.token = token

    def build_body(self):
        mp_config = self.mp_config()

        log.debug("Downloading image from Instagram")
        media_fh = urlopen(self.pic.picture_url)
        filename = basename(urlparse(self.pic.picture_url).path)
        photo_url = self.post_media(mp_config["media-endpoint"], media_fh, filename)

        body = {
            "content": self.pic.text,
            "h": "entry",
            "photo": photo_url,
            "syndication": DETAIL_URL.format(self.pic.code),
            "published": datetime.fromtimestamp(self.pic.timestamp).isoformat(),
            "mp-syndicate-to": "twitter"  # Should read from mp_config
        }
        return body

    def mp_config(self):
        log.debug("Discovering Micropub config")
        request = Request(self.endpoint + "?" + urlencode({"q": "config"}), headers={
            "Authorization": "Bearer {}".format(self.token)
        })
        mp_config = urlopen(request).read().decode("utf-8")
        mp_config = json.loads(mp_config)
        return mp_config

    def post_media(self, media_endpoint, media_fh, filename):
        photo_url = _upload_media(media_endpoint, media_fh, self.token, filename)
        return photo_url

    def post(self):
        body = self.build_body()
        log.debug("Posting to {}".format(self.endpoint))
        body = urlencode(body).encode("utf-8")
        request = Request(self.endpoint, data=body, headers={
            "Authorization": "Bearer {}".format(self.token),
        })
        response = urlopen(request)
        if response.status == 201:
            post_url = response.headers.get("Location")
            log.debug("Uploaded: {}".format(post_url))
            return post_url
        else:
            return response.reason


def encode_multipart_formdata(fh, filename):
    """
    Can't believe I need to spell out this function, but this is needed in order
    to upload a file using urllib.urlopen. Note that this will perform the
    upload with a single file under the `file` form field.

    :param fh: File-like object
    :param filename: filename of such file
    :return: (content type string, body bytes as needed by urlopen)
    """
    BOUNDARY = b"________ThIs_Is_tHe_bouNdaRY_$"
    lines = []
    value = fh.read()
    filename = filename.encode("utf-8")
    lines.append(b"--" + BOUNDARY)
    lines.append(b'Content-Disposition: form-data; name="file"; filename= "' + filename + b'"')
    lines.append(b"Content-Type: image/jpeg")
    lines.append(b"")
    lines.append(value)
    lines.append(b"--" + BOUNDARY + b"--")
    lines.append(b"")
    body = b"\r\n".join(lines)
    content_type = "multipart/form-data; boundary={}".format(BOUNDARY.decode("utf-8"))
    return content_type, body


def _upload_media(media_endpoint, media_fh, token, filename):
    """
    :param media_endpoint: URL where to POST the upload
    :param media_fh: file-like object to upload
    :param token: Bearer token for the MP media endpoint
    :param filename: Filename of identify the file as to the server
    :return: URL of the uploaded file
    """
    log.debug("Uploading picture to media endpoint")
    content_type, body = encode_multipart_formdata(media_fh, filename)
    request = Request(media_endpoint, data=body, headers={
        "Authorization": "Bearer {}".format(token),
        "Content-Type": content_type,
    })
    response = urlopen(request)
    if response.status == 201:
        photo_url = response.headers.get("Location")
        log.debug("Uploaded: {}".format(photo_url))
        return photo_url
    else:
        log.error("Failed to upload media: {}".format(response.reason))
        raise ValueError(response.reason)


def main(config, pic_id):
    profile_url = PROFILE.format(config["user"])
    mp_endpoint = config["endpoint"]
    token = config["token"]

    html = fetch_profile(profile_url)
    doc = parse(html)
    pictures = extract_pictures(doc)
    pictures = [IGPic(n) for n in pictures]
    pic = [p for p in pictures if p.code in pic_id][0]
    post = Post(pic, mp_endpoint, token)
    post.post()


def _run_script():
    """
    Wrapper function because we don't want anything else in the global scope.
    """
    config_file = sys.argv[1]
    _pic_id = sys.argv[2]
    _config = json.load(open(config_file))
    main(_config, _pic_id)


if __name__ == "__main__":
    _run_script()
