# uGram
Microgram: Instagram to Micropub

Given that Ownyourgram.com is having trouble being throttled by Instagram, I
gave it a shot to make something that I could use myself given that the single
use case is much simpler than the multi-tenancy nature of OYG.

My goal with this is to keep the script with 0 external dependencies, so that
anybody can just run this directly with a Python 3 interpreter (requires Python 3.7+).

A good side effect is that this script can be easily run as a Lambda job that
only takes care of your own IG profile and your MP blog.

## How to use

You need to generate a JSON file with the following shape:

```json
{
  "user": "<your Instagram handle>",
  "endpoint": "<Your Micropub endpoint>",
  "token": "<Bearer token for your MP endpoint>"
}
```

**Security Note**: Keep your config.json file secure and never commit it to version control. Consider using environment variables for sensitive tokens in production.

Then run the following in your terminal:

```bash
# Post a single Instagram URL
$ python ugram.py config.json <IG post URL>

# Post multiple Instagram URLs
$ python ugram.py config.json <IG post URL 1> <IG post URL 2>

# Dry-run mode (preview without posting)
$ python ugram.py config.json <IG post URL> --commit=False

# Set custom publish date
$ python ugram.py config.json <IG post URL> -d 2024-01-15

# Disable syndication to other platforms
$ python ugram.py config.json <IG post URL> --syndicate=False
```

**Options:**
- `-d, --date`: Publish date in ISO format (YYYY-MM-DD). Defaults to the post's original date.
- `--commit`: Whether to actually post (True) or dry-run (False). Default: True
- `--syndicate`: Whether to syndicate to configured platforms (Twitter, Mastodon). Default: True

## Media upload

uGram will download the JPEG file from Instagram and upload it to your media 
endpoint. The media endpoint will be discovered through Micropub config 
discovery, so your Micropub endpoint must support this feature.


# The HARness utility

This utility is useful for bulk uploading photos. It processes a .har file that can be 
obtained through the browser DevTools Network panel.

**How to capture a HAR file:**
1. Open your Instagram profile page in a browser
2. Open DevTools (F12) and go to the Network tab
3. Scroll through your profile to load posts
4. Filter the requests for `query` endpoint
5. Right-click and save the requests as a HAR file

**Usage:**

```bash
# Dry-run mode (preview posts without uploading)
$ python HARness.py config.json ig_queries.har --from=2023/01/01 --to=2023/12/31

# Actually post to your blog
$ python HARness.py config.json ig_queries.har --from=2023/01/01 --to=2023/12/31 --commit

# Post with syndication enabled
$ python HARness.py config.json ig_queries.har --from=2023/01/01 --to=2023/12/31 --commit --syndicate
```

It leverages the same `config.json` file as ugram.py. It allows you to upload
only posts within a specific date range instead of everything in the archive.

**Options:**
- `--from`: Start date (required, format: YYYY/MM/DD)
- `--to`: End date (required, format: YYYY/MM/DD)
- `--commit`: If present, posts will be uploaded. If omitted, runs in dry-run mode. Default: False
- `--syndicate`: If present, posts will syndicate to configured platforms. Default: False

