"""DRIVE_COMMAND: the Pi's proposed drive input for the Bone.

Payload (JSON):

    {"x": steer, -1..1, + = turn right,
     "y": drive, -1..1, + = forward,
     "ttl_ms": how long the Bone may apply it, 50..1000}

The Pi only proposes. balance_bot keeps balancing no matter what, applies this
only while its Pi-drive gate is open and the SBUS stick is centred, and treats
an expired command exactly like a centred stick. It travels as an
unacknowledged EVENT at roughly 10 Hz: a lost packet is simply superseded by
the next one, and silence from the Pi stops the robot within ttl_ms.
"""
import json, math

TTL_MIN_MS, TTL_MAX_MS, TTL_DEFAULT_MS = 50, 1000, 300

def parse_drive(data):
    """Validate a drive command dict; returns a clean copy or raises ValueError."""
    try:
        x=float(data["x"]); y=float(data["y"])
        ttl=int(data.get("ttl_ms",TTL_DEFAULT_MS))
    except (KeyError,TypeError,ValueError) as exc:
        raise ValueError(f"bad drive command: {exc}") from None
    if not (math.isfinite(x) and math.isfinite(y)): raise ValueError("non-finite drive value")
    return {"x":round(max(-1.0,min(1.0,x)),4),"y":round(max(-1.0,min(1.0,y)),4),
            "ttl_ms":max(TTL_MIN_MS,min(TTL_MAX_MS,ttl))}

def encode_drive(cmd):
    return json.dumps(cmd,separators=(",",":")).encode()
