# uGram
Microgram: Instagram to Micropub

Given that Ownyourgram.com is having trouble being throthled by Instagram. I
gave it a shot to make something that I could use myself given that the single
use case is much simpler than the multi-tenance nature of OYG.

My goal with this is to keep the script with 0 external dependencies, so that
anybody can just run this directly with a Python(3) interpreter.

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

Then run the following in your terminal:

```
$ python ugram.py config.json <IG post URL>
```

## Media upload

uGram will download the Jpeg file from instagram and upload it to your media 
endpoint. The Media endpoint will be discovered through Micropub config 
discovery, so that will need to be supported.
