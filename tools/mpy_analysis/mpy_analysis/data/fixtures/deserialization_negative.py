import json


def decode_payload(payload: str) -> object:
    return json.loads(payload)
