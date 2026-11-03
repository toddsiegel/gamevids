from pathlib import Path

from gamevids.dji import serial_from_packet

# First `djmd` packet from DJI_20261024115345_0001_D.MP4 (Osmo Action 5 Pro).
REAL_PACKET = (Path(__file__).parent / "data" / "djmd_first_packet.bin").read_bytes()


def field(number: int, payload: bytes) -> bytes:
    assert len(payload) < 128
    return bytes([number << 3 | 2, len(payload)]) + payload


def packet(header: bytes) -> bytes:
    """Wrap header fields the way the camera does: packet -> frame (1) -> header (1)."""
    return field(1, field(1, header))


def test_reads_serial_from_real_packet():
    # `strings` shows "82JANA9MLDSCN32" here; the trailing "2" is the next field's tag byte.
    assert serial_from_packet(REAL_PACKET) == "82JANA9MLDSCN3"


def test_skips_other_fields_to_find_serial():
    header = field(1, b"dvtm.proto") + bytes([0x48, 0xA7, 0x01]) + field(5, b"SERIAL123")
    assert serial_from_packet(packet(header)) == "SERIAL123"


def test_returns_none_without_serial():
    assert serial_from_packet(packet(field(1, b"dvtm.proto"))) is None


def test_returns_none_for_garbage_or_truncated_data():
    assert serial_from_packet(b"") is None
    assert serial_from_packet(b"\xff\xff\xff") is None
    assert serial_from_packet(REAL_PACKET[:40]) is None


def test_rejects_non_alphanumeric_serial():
    assert serial_from_packet(packet(field(5, b"\x00\x01bad"))) is None
