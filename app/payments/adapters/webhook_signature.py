"""Validação da assinatura dos webhooks do Mercado Pago.

Cabeçalho `x-signature: ts=<timestamp>,v1=<hmac>`. O HMAC-SHA256 é calculado
com a chave secreta do painel (Webhooks) sobre o manifesto
`id:<data.id em minúsculas>;request-id:<x-request-id>;ts:<ts>;`, omitindo as
partes que não vierem.
"""

import hashlib
import hmac


def build_manifest(data_id: str | None, request_id: str | None, ts: str | None) -> str:
    parts = []
    if data_id:
        parts.append(f"id:{data_id.lower()};")
    if request_id:
        parts.append(f"request-id:{request_id};")
    if ts:
        parts.append(f"ts:{ts};")
    return "".join(parts)


def verify_signature(secret: str, x_signature: str | None, request_id: str | None, data_id: str | None) -> bool:
    if not x_signature:
        return False
    fields = dict(part.strip().split("=", 1) for part in x_signature.split(",") if "=" in part)
    ts, received = fields.get("ts"), fields.get("v1")
    if not ts or not received:
        return False
    expected = hmac.new(secret.encode(), build_manifest(data_id, request_id, ts).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)
