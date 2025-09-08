import hmac, hashlib

def compute_hmac_sha256_hex(secret: str, body: bytes) -> str:
    mac = hmac.new(secret.encode('utf-8'), body, hashlib.sha256)
    return mac.hexdigest()
