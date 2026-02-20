import re
import json
import argparse
import logging as log
from html import unescape
from os.path import basename
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.parse import urlencode, urlparse, urljoin


PROFILE_URL = "https://www.instagram.com/{}"
DETAIL_URL = "https://www.instagram.com/p/{}/"
EMBED_URL = "https://www.instagram.com/p/{}/embed/"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:71.0) Gecko/20100101 Firefox/71.0"
REQ_HEADERS = {"User-Agent": UA}

log.basicConfig(level=log.DEBUG)


def str2bool(v):
    """Parses flexible boolean inputs."""
    if isinstance(v, bool):
       return v
    if v.lower() in {'yes', 'true', 't', 'y', '1'}:
        return True
    elif v.lower() in {'no', 'false', 'f', 'n', '0'}:
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected (True/False).')


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


def extract_pictures(payload):
    """
    Use this function to get the list of publications from a users' profile
    HTML page.

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
        self.text = node["text"]

        # If carousel_urls exist, then that will contain the main photo
        # already, so no need to use both
        if "carousel_urls" in node:
            self.picture_urls = node["carousel_urls"]
        else:
            self.picture_urls = [node["image_url"]]

        publish_date = None
        if "created_at" in node:
            timestamp = node["created_at"]
            publish_date = datetime.fromtimestamp(timestamp)

        self.publish_date = publish_date

    @classmethod
    def fetch_ig_post_data(cls, pub_url):
        detail_html = cls.fetch_html(pub_url)
        embed_html = cls.fetch_html(urljoin(pub_url, "embed/"))
        pic_data = cls.parse(detail_html, embed_html)
        code = pub_url.split("/")[-2]
        pic_data["code"] = code
        pic_data["post_url"] = pub_url
        return pic_data

    @classmethod
    def fetch_html(cls, url):
        log.debug("Reading Instagram: {}".format(url))
        req = Request(url, headers=REQ_HEADERS)
        raw_html = urlopen(req).read().decode("utf-8")
        return raw_html

    @classmethod
    def parse(cls, raw_html, embed_html):
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

        img_urls = []
        # Iterate for the main image
        for line in embed_html.split("\n"):
            if "EmbeddedMediaImage" in line:
                img_url = line.split("src=")[2][1:].split('"')[0]
                img_url = unescape(img_url)
                img_urls.append(img_url)
                break

        # Now look for additional images in the post
        line = embed_html.split("\n")[11]
        js_content = re.sub(r'<script[^>]*>(.*?)', "", line, re.DOTALL)
        js_content = re.search(r's\.handle\((.*?)\);requireLazy', js_content, re.DOTALL)
        if js_content:
            data = json.loads(js_content.group(1))
            data = json.loads(data["require"][1][3][0]["contextJSON"])
            edges = data['context']['media']['edge_sidecar_to_children']['edges']
            more_pics = [edge['node']["display_resources"][-1]["src"] for edge in edges if "display_resources" in edge["node"]]
            img_urls.extend(more_pics)

        assert img_url is not None, "No image found from Instagram post HTML."
        result = {
            "text": description,
            "image_url": img_url,
            "is_video": is_video,
            "video_url": None,
        }
        if len(img_urls) > 1:
            result["carousel_urls"] = img_urls
        return result

    @classmethod
    def from_url(cls, post_url):
        post_data = cls.fetch_ig_post_data(post_url)
        return cls(post_data)

    @classmethod
    def from_filtered_node(cls, node) -> "IGPost":
        """
        Use this when using the HARness.py file filtered nodes
        """
        return cls(node)


class Post:
    """
    Represents a publication in a Micropub blog.
    """

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

    def build_body(self, uploaded_urls, syndicate):
        post_content = self.ig_post.text
        main_photo, more_photos = uploaded_urls[0], uploaded_urls[1:]
        if more_photos:
            more_photos = ["![]({})".format(purl) for purl in more_photos]
            post_content = post_content + "\n\n" + "\n".join(more_photos)

        unique_keys = {
            "content": post_content,
            "h": "entry",
            "photo": main_photo,
            "syndication": DETAIL_URL.format(self.ig_post.code),
        }
        multi_keys = []
        if syndicate:
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

    def print(self, site, syndicate):
        body = self.build_body(self.ig_post.picture_urls, False)
        body = dict(body)
        log.debug("Would post to: {}".format(site.endpoint))
        log.debug("Text: {}".format(body["content"]))
        log.debug("Syndicate: {}".format(syndicate))
        log.debug("Photos to post:")
        log.debug("\n".join(self.ig_post.picture_urls))
        if not self.publish_date:
            log.debug("No Date, will use today")
        else:
            log.debug("Publish as: {}".format(self.publish_date))

    def post(self, site, syndicate):
        uploaded_urls = self.upload_media(site)
        body = self.build_body(uploaded_urls, syndicate)
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


def post_single_ig_post(site, pub_url, publish_date, syndicate, commit):
    # A single IG post can have multiple pictures.
    ig_post = IGPost.from_url(pub_url)
    publish_date = publish_date or ig_post.publish_date
    post = Post(ig_post, publish_date)
    if commit:
        post.post(site, syndicate)
    else:
        post.print(site, syndicate)


def run_script(config, publication_urls, publish_date, syndicate, commit):
    """
    Wrapper function because we don't want anything else in the global scope.
    """
    mp_endpoint = config["endpoint"]
    token = config["token"]
    site = MicroPubSite(mp_endpoint, token)
    commit = False
    for pub_url in publication_urls:
        post_single_ig_post(site, pub_url, publish_date, syndicate, commit)
    log.info("Done!")


def parse_args():
    parser = argparse.ArgumentParser(
        prog="uGram", description="Micropub post from Instagram")
    parser.add_argument('config',
        type=argparse.FileType("r", encoding="utf-8"))
    parser.add_argument('urls', nargs="+")
    parser.add_argument('-d', '--date', default=None)
    parser.add_argument('--commit', type=str2bool, nargs='?', const=True, default=True)
    parser.add_argument('--syndicate', type=str2bool, nargs='?', const=True, default=True)
    args = parser.parse_args()
    return args


def main():
    args = parse_args()
    config = json.load(args.config)
    publish_date = datetime.fromisoformat(args.date) if args.date else None
    publication_urls = args.urls
    syndicate = args.syndicate
    commit = args.commit

    run_script(config, publication_urls, publish_date, syndicate, commit)


if __name__ == "__main__":
    """
    uGram - Instagram to Micropub Publisher

    Posts Instagram content to your Micropub-enabled blog/website.

    Usage:
        python ugram.py config.json <IG_POST_URL> [<IG_POST_URL> ...] [OPTIONS]

    Arguments:
        config       Path to JSON config file with 'endpoint', 'token', and 'user' fields
        urls         One or more Instagram post URLs to publish

    Options:
        -d, --date DATE       Publish date in ISO format (YYYY-MM-DD). Defaults to post's original date
        --commit BOOL         Whether to actually post (True) or dry-run (False). Default: True
        --syndicate BOOL      Whether to syndicate to configured platforms. Default: True

    Examples:
        python ugram.py config.json https://www.instagram.com/p/ABC123/
        python ugram.py config.json https://www.instagram.com/p/ABC123/ --commit=False
        python ugram.py config.json https://www.instagram.com/p/ABC123/ -d 2024-01-15 --syndicate=False
    """
    main()
