import sys
import json
import argparse
import logging as log
from html import unescape
from os.path import basename
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.parse import urlencode, urlparse, urljoin


PROFILE = "https://www.instagram.com/{}"
DETAIL_URL = "https://www.instagram.com/p/{}/"
EMBED_URL = "https://www.instagram.com/p/{}/embed/"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:71.0) Gecko/20100101 Firefox/71.0"


log.basicConfig(level=log.DEBUG)


def fetch_ig_post_data(pic_url):
    detail_html = fetch_html(pic_url)
    embed_html = fetch_html(urljoin(pic_url, "embed/"))
    pic_data = parse(detail_html, embed_html)
    code = pic_url.split("/")[-2]
    pic_data["code"] = code
    pic_data["post_url"] = pic_url
    return pic_data


def fetch_html(url):
    log.debug("Reading Instagram: {}".format(url))
    req = Request(url, headers={
        "User-Agent": UA
    })
    raw_html = urlopen(req).read().decode("utf-8")
    return raw_html


def parse(raw_html, embed_html):
    """
    :param raw_html: raw HTML for the detail page
    :param embed_html: raw HTML for the embed page
    :return: Dict with user's data
    """
    description, img_url, is_video = None, None, False
    for line in raw_html.split("\n"):
        if "og:title" in line:
            description = line.split('"og:title"')[1][10:].split('"')[0]
            description = unescape(description)
            description = description.split(": ")[1].strip('"')
            break

    for line in embed_html.split("\n"):
        if "EmbeddedMediaImage" in line:
            img_url = line.split("src=")[2][1:].split('"')[0]
            img_url = unescape(img_url)

    import pdb;pdb.set_trace()
    return {
        "image_url": img_url,
        "description": description,
        "is_video": is_video
    }


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
    return [p["node"] for p in pictures]


class MicroPubSite:
    """
    Discovers the Micropub config available from the site.
    """

    def __init__(self, endpoint, token):
        self.endpoint = endpoint
        self.token = token
        self.headers = {"Authorization": "Bearer {}".format(self.token)}
        self.mp_config = self.fetch_mp_config()

    def fetch_mp_config(self):
        log.debug("Discovering Micropub config")
        url = self.endpoint + "?" + urlencode({"q": "config"})
        request = Request(url, headers=self.headers)
        mp_config = urlopen(request).read().decode("utf-8")
        mp_config = json.loads(mp_config)
        return mp_config


class IGPost:
    """
    A wrapper around the IG bare HTML JSON structure, in order to strip out
    the attributes we care for.
    """
    def __init__(self, node):
        self.code = node["code"]
        self.video = node["is_video"]
        self.text = node["description"]
        self.picture_urls = [node["image_url"]]


class Post:
    def __init__(self, ig_post, publish_date):
        self.ig_post = ig_post
        self.publish_date = publish_date

    def upload_media(self, site):
        log.debug("Downloading images from Instagram")
        uploaded_urls = []
        for picture_url in self.ig_post.picture_urls:
            media_fh = urlopen(picture_url)
            filename = basename(urlparse(picture_url).path)
            photo_url = self.post_media(site, media_fh, filename)
            uploaded_urls.append(photo_url)
        return uploaded_urls

    def build_body(self, uploaded_urls):
        unique_keys = {
            "content": self.ig_post.text,
            "h": "entry",
            "photo": uploaded_urls,
            "syndication": DETAIL_URL.format(self.ig_post.code),
        }
        multi_keys = [
            ("mp-syndicate-to", "twitter"),  # Should read from mp_config
            ("mp-syndicate-to", "mastodon"),
        ]
        body = list(unique_keys.items())
        body.extend(multi_keys)
        if self.publish_date:
            publish_date = self.publish_date.isoformat()
            body.append(("published", publish_date))
        return body

    def post_media(self, site, media_fh, filename):
        media_endpoint = site.mp_config["media-endpoint"]
        photo_url = _upload_media(media_endpoint, media_fh, site.token, filename)
        return photo_url

    def post(self, site):
        uploaded_urls = self.upload_media(site)
        body = self.build_body(uploaded_urls)
        log.debug("Posting to {}".format(site.endpoint))
        body = urlencode(body, doseq=True).encode("utf-8")
        request = Request(site.endpoint, data=body, headers=site.headers)
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


def post_single_ig_post(site, pic_url, publish_date):
    # A single IG post can have multiple pictures.
    post_data = fetch_ig_post_data(pic_url)
    if not publish_date and "taken_at_timestamp" in post_data:
        timestamp = node["taken_at_timestamp"]
        publish_date = datetime.fromtimestamp(timestamp).isoformat()

    ig_post = IGPost(post_data)
    post = Post(ig_post, publish_date)
    post.post(site)


def _run_script(config, pic_urls, publish_date):
    """
    Wrapper function because we don't want anything else in the global scope.
    """
    mp_endpoint = config["endpoint"]
    token = config["token"]
    site = MicroPubSite(mp_endpoint, token)
    for pic_url in pic_urls:
        post_single_ig_post(site, pic_url, publish_date)
    log.info("Done!")


def parse_args():
    parser = argparse.ArgumentParser(
        prog="uGram", description="Micropub post from Instagram")
    parser.add_argument('config',
        type=argparse.FileType("r", encoding="utf-8"))
    parser.add_argument('urls', nargs="+")
    parser.add_argument('-d', '--date', default=None)
    args = parser.parse_args()
    return args


def __main():
    args = parse_args()
    config = json.load(args.config)
    publish_date = datetime.froisoformat(args.date) if args.date else None
    pic_urls = args.urls

    _run_script(config, pic_urls, publish_date)


if __name__ == "__main__":
    __main()
