#!/usr/bin/env python3
"""Cyclops durable encrypted production and transaction storage.

The GitHub repository is PUBLIC. Never commit unencrypted draft contents,
customer data, or paid deliverables to it. AES-GCM ciphertext can be stored in
the public repository and decrypted only with existing runner-side secrets.
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

REPO_ROOT="ops/cyclops-vault"
PENDING=REPO_ROOT+"/pending.json"
LEDGER="ops/cyclops-ledger.json"


def _key():
    # Dedicated secret recommended; current runner can use its already-installed
    # AgentMail secret until CYCLOPS_ASSET_KEY is configured. Changing either
    # without migrating the vault makes old artifacts undecryptable.
    key=os.environ.get("CYCLOPS_ASSET_KEY") or os.environ.get("AGENTMAIL_API_KEY")
    if not key:
        raise RuntimeError("cyclops_vault_secret_missing")
    return hashlib.sha256(b"cyclops-aes256-gcm-v1:"+key.encode()).digest()


def _endpoint(path):
    repo=os.environ.get("GITHUB_REPOSITORY","")
    token=os.environ.get("GH_TOKEN","")
    if "/" not in repo or not token:
        raise RuntimeError("cyclops_vault_github_token_missing")
    if not path.startswith(("ops/cyclops-vault/","ops/cyclops-ledger.json")):
        raise ValueError("cyclops_vault_path_outside_namespace")
    url="https://api.github.com/repos/"+repo+"/contents/"+urllib.parse.quote(path,safe="/")
    headers={"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json",
             "X-GitHub-Api-Version":"2022-11-28","User-Agent":"cyclops-vault/1.0"}
    return url,headers


def _read(path):
    url,headers=_endpoint(path)
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=20) as response:
            doc=json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code==404:
            return None,None
        raise
    return base64.b64decode(doc["content"].replace("\n","")),doc["sha"]


def _write(path,content):
    url,headers=_endpoint(path)
    for attempt in range(3):
        _,sha=_read(path)
        doc={"message":"Save Cyclops private business progression",
             "branch":"main",
             "content":base64.b64encode(content).decode()}
        if sha:
            doc["sha"]=sha
        req=urllib.request.Request(url,data=json.dumps(doc).encode(),method="PUT",
                                   headers={**headers,"Content-Type":"application/json"})
        try:
            with urllib.request.urlopen(req,timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in (409,422) and attempt<2:
                continue
            raise


def _wrap(value):
    salt=os.urandom(12)
    plain=json.dumps(value,ensure_ascii=False,separators=(",",":")).encode()
    cipher=AESGCM(_key()).encrypt(salt,plain,b"CYCLOPS_VAULT_1")
    return (json.dumps({"format":"aes-256-gcm","version":1,
                        "nonce":base64.b64encode(salt).decode(),
                        "ciphertext":base64.b64encode(cipher).decode()},
                       separators=(",",":"))+"\n").encode()


def _unwrap(raw):
    if raw is None:
        return None
    doc=json.loads(raw)
    if doc.get("format")!="aes-256-gcm" or doc.get("version")!=1:
        raise ValueError("unrecognized_cyclops_vault_format")
    nonce=base64.b64decode(doc["nonce"])
    cipher=base64.b64decode(doc["ciphertext"])
    return json.loads(AESGCM(_key()).decrypt(nonce,cipher,b"CYCLOPS_VAULT_1"))


def save_private(path,value):
    _write(path,_wrap(value))
    return path


def read_private(path):
    raw,_=_read(path)
    return _unwrap(raw)


def read_pending():
    return read_private(PENDING) or {"stage":"empty"}


def save_pending(value):
    return save_private(PENDING,value)


def asset_vault_path(asset_id):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{10,100}",str(asset_id)):
        raise ValueError("invalid_asset_identifier")
    return REPO_ROOT+"/assets/"+asset_id+".json"


def _normalize_file_record(item):
    """Convert real bytes into an integrity-checked encrypted package record."""
    if isinstance(item,dict):
        name=str(item.get("filename") or "")
        coded=str(item.get("content_b64") or "")
        try:
            data=base64.b64decode(coded,validate=True)
        except (ValueError,base64.binascii.Error):
            raise ValueError("artifact_invalid_base64:"+name) from None
        if (not data or hashlib.sha256(data).hexdigest()!=item.get("sha256")
                or len(data)!=item.get("size_bytes")):
            raise ValueError("artifact_integrity_failed:"+name)
        return {"filename":name,"content_b64":coded,"sha256":item["sha256"],
                "size_bytes":len(data),"content_type":item.get("content_type") or "application/octet-stream"}
    # Older plaintext drafts are supported only for recovery; new manufacture
    # supplies authenticated bytes from the isolated production workshop.
    if isinstance(item,(list,tuple)) and len(item)==2:
        name,content=item
        raw=str(content).encode("utf-8")
        return {"filename":str(name),"content_b64":base64.b64encode(raw).decode(),
                "sha256":hashlib.sha256(raw).hexdigest(),
                "size_bytes":len(raw),"content_type":"text/plain"}
    raise ValueError("invalid_artifact_record")


def seal_asset(asset_id,plan):
    """Save and confirm fully finished binary-safe product before checkout."""
    path=asset_vault_path(asset_id)
    expected=[_normalize_file_record(x) for x in plan["_files"]]
    save_private(path,{"asset_id":asset_id,"files":expected,
                       "price_usd":plan["price_usd"],
                       "title":plan["title"],"sealed":True})
    persisted=read_private(path)
    if persisted is None or persisted.get("files")!=expected:
        raise RuntimeError("cyclops_vault_verification_failed")
    return path


def read_asset_files(asset_id):
    item=read_private(asset_vault_path(asset_id))
    if not item or not item.get("sealed") or item.get("asset_id")!=asset_id:
        raise RuntimeError("cyclops_paid_asset_unavailable")
    files=[]
    for entry in item["files"]:
        record=_normalize_file_record(entry)
        files.append((record["filename"],base64.b64decode(record["content_b64"],validate=True)))
    return files


def read_ledger():
    # Ledger contains hashes of payment IDs and amounts, never buyer PII.
    raw,_=_read(LEDGER)
    if raw is None:
        return {"version":1,"paid":{},"fulfilled":{}}
    obj=json.loads(raw)
    if obj.get("version")!=1:
        raise RuntimeError("cyclops_ledger_version_unknown")
    return obj


def record_order(session_id,amount_cents,status):
    ident=hashlib.sha256(str(session_id).encode()).hexdigest()
    for attempt in range(3):
        ledger=read_ledger()
        group=ledger.setdefault(status,{})
        if ident in group:
            return False
        group[ident]=int(amount_cents)
        try:
            _write(LEDGER,(json.dumps(ledger,separators=(",",":"))+"\n").encode())
            return True
        except urllib.error.HTTPError as exc:
            if exc.code not in (409,422) or attempt==2:
                raise
    return False
