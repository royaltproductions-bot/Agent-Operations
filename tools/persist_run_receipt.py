"""Persist immutable receipts despite concurrent repository writers."""
import base64
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request


def persist(path, message, opener=urllib.request.urlopen, sleep=time.sleep):
    data=Path(path).read_bytes()
    repo=os.environ['GITHUB_REPOSITORY']
    token=os.environ['GH_TOKEN']
    url=f'https://api.github.com/repos/{repo}/contents/{path}'
    headers={'Authorization':'Bearer '+token, 'Accept':'application/vnd.github+json',
             'Content-Type':'application/json'}
    for attempt in range(5):
        try:
            try:
                with opener(urllib.request.Request(url+'?ref=main',headers=headers),timeout=30) as response:
                    existing=json.load(response)
                if base64.b64decode(existing['content'])==data:
                    return
                raise RuntimeError('immutable_receipt_already_exists_with_different_content')
            except urllib.error.HTTPError as exc:
                if exc.code!=404:
                    raise
            payload={'message':message,'branch':'main','content':base64.b64encode(data).decode()}
            with opener(urllib.request.Request(url,data=json.dumps(payload).encode(),
                                              headers=headers,method='PUT'),timeout=30):
                return
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            if isinstance(exc,urllib.error.HTTPError) and exc.code not in (409,429,500,502,503,504):
                raise
            if attempt==4:
                raise
            delay=2**attempt
            if isinstance(exc,urllib.error.HTTPError):
                try: delay=max(delay,float(exc.headers.get('Retry-After','0')))
                except (ValueError,TypeError): pass
            sleep(delay)


if __name__=='__main__':
    persist(sys.argv[1],sys.argv[2])
