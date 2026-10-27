"""Immutable private COS objects; database BYTEA fields contain references, not files."""
import hashlib
import os
import re
from functools import lru_cache

MAGIC = b'RSMS-COS1:'


@lru_cache(maxsize=1)
def client():
    from qcloud_cos import CosConfig, CosS3Client
    return CosS3Client(CosConfig(Region=os.environ['COS_REGION'], SecretId=os.environ['COS_SECRET_ID'], SecretKey=os.environ['COS_SECRET_KEY'], Scheme='https', Timeout=20))


def key(digest):
    return os.environ.get('COS_PREFIX', 'rsms/').strip('/') + '/objects/' + digest[:2] + '/' + digest


def put(content):
    if content is None or not os.environ.get('COS_BUCKET'):
        return content
    digest = hashlib.sha256(content).hexdigest()
    client().put_object(Bucket=os.environ['COS_BUCKET'], Key=key(digest), Body=content, ContentType='application/octet-stream', ACL='private')
    return MAGIC + str(len(content)).encode() + b':' + digest.encode()


def reference(value):
    return re.fullmatch(rb'RSMS-COS1:(\d+):([a-f0-9]{64})', bytes(value)) if value is not None else None


def get(value):
    if value is None:
        return None
    value = bytes(value)
    ref = reference(value)
    if not ref:
        return value
    response = client().get_object(Bucket=os.environ['COS_BUCKET'], Key=key(ref[2].decode()))
    stream = response['Body'].get_raw_stream()
    try:
        content = stream.read()
    finally:
        stream.close()
    if len(content) != int(ref[1]) or hashlib.sha256(content).hexdigest().encode() != ref[2]:
        raise RuntimeError('COS object integrity check failed')
    return content


def size(value):
    ref = reference(value)
    return int(ref[1]) if ref else len(value)
