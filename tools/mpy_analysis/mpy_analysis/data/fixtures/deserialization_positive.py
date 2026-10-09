import pickle


def decode_payload(payload: bytes) -> object:
    return pickle.loads(payload)
