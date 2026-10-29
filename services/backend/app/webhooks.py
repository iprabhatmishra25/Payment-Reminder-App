"""Verify Resend/Svix signatures against the original body before accepting events."""
import base64
import hashlib
import hmac
import json
import time
from fastapi import HTTPException


def verify(body: bytes, headers, secret: str):
    if not secret:
        raise HTTPException(503, 'Webhook verification is not configured')
    try:
        event_id=headers['svix-id']
        timestamp=headers['svix-timestamp']
        if abs(time.time()-int(timestamp))>300 or len(event_id)>150:
            raise ValueError('Expired signature')
        key=base64.b64decode(secret.removeprefix('whsec_'),validate=True)
        signature=base64.b64encode(hmac.new(key,event_id.encode()+b'.'+timestamp.encode()+b'.'+body,hashlib.sha256).digest()).decode()
        candidates=[part[3:] for part in headers['svix-signature'].split() if part.startswith('v1,')]
        if not any(hmac.compare_digest(candidate,signature) for candidate in candidates):
            raise ValueError('Invalid signature')
        return event_id,json.loads(body)
    except (KeyError,ValueError,TypeError):
        raise HTTPException(400,'Invalid webhook signature or payload')


def delivery_status(current,event_types):
    if 'email.bounced' in event_types or 'email.complained' in event_types:
        return 'BOUNCED'
    if 'email.delivered' in event_types:
        return 'DELIVERED' if current!='BOUNCED' else current
    return current
