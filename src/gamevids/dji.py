"""Read the camera serial from a DJI `djmd` metadata packet.

The DJI Osmo Action cameras write a protobuf message per frame into a data
stream tagged `djmd`. The header of each message holds the camera model,
firmware and serial number. There is no published schema, so this reads the
wire format directly and only looks at the field we need:

    message 1 (frame) -> message 1 (header) -> string 5 (serial)
"""

from collections.abc import Iterator

SERIAL_PATH = (1, 1, 5)

VARINT, FIXED64, LENGTH, FIXED32 = 0, 1, 2, 5


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("truncated varint")
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def iter_fields(buf: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """Yield `(field_number, wire_type, value)` for each top-level field."""
    pos = 0
    while pos < len(buf):
        key, pos = _read_varint(buf, pos)
        field, wire = key >> 3, key & 7
        if wire == VARINT:
            value, pos = _read_varint(buf, pos)
        elif wire in (FIXED64, FIXED32, LENGTH):
            if wire == LENGTH:
                size, pos = _read_varint(buf, pos)
            else:
                size = 8 if wire == FIXED64 else 4
            if pos + size > len(buf):
                raise ValueError("truncated field")
            value, pos = buf[pos : pos + size], pos + size
        else:
            raise ValueError(f"unsupported wire type {wire}")
        yield field, wire, value


def _find(buf: bytes, path: tuple[int, ...]) -> bytes | None:
    for field, wire, value in iter_fields(buf):
        if field == path[0] and wire == LENGTH:
            assert isinstance(value, bytes)
            return value if len(path) == 1 else _find(value, path[1:])
    return None


def serial_from_packet(packet: bytes) -> str | None:
    """Return the camera serial in a `djmd` packet, or None if it isn't there."""
    try:
        raw = _find(packet, SERIAL_PATH)
    except ValueError:
        return None
    if not raw:
        return None
    try:
        serial = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    return serial if serial.isalnum() else None
