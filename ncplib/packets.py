import warnings
from collections import namedtuple, OrderedDict
from struct import Struct
from ncplib.errors import DecodeError, DecodeWarning
from ncplib.helpers import unix_to_datetime, datetime_to_unix
from ncplib.values import encode_value, decode_value


# Packet structs.

PACKET_HEADER_STRUCT = Struct("<4s4sII4sII4s")

FIELD_HEADER_STRUCT = Struct("<4s3s1sI")

PARAM_HEADER_STRUCT = Struct("<4s3sB")

PACKET_FOOTER_STRUCT = Struct("<I4s")


# Identifier decoding.

def decode_identifier(value):
    return value.rstrip(b" \x00").decode("latin1")


# Field decoding.

FieldData = namedtuple("FieldData", ("name", "id", "params",))


def decode_fields(buf, offset, field_limit):
    fields = []
    while offset < field_limit:
        # Decode field header.
        field_name, field_size, field_type_id, field_id = FIELD_HEADER_STRUCT.unpack_from(buf, offset)
        param_limit = offset + int.from_bytes(field_size, "little") * 4
        offset += 12  # 12 is the size of the field header.
        # Decode params.
        params = OrderedDict()
        while offset < param_limit:
            # HACK: Work around a known garbled NCP packet problem from Axis nodes.
            if buf[offset:offset+8] == b"\x00\x00\x00\x00\xaa\xbb\xcc\xdd":
                warnings.warn(DecodeWarning("Encountered embedded packet footer bug"))
                offset += 8
                continue
            # Decode the param header.
            param_name, param_size, param_type_id = PARAM_HEADER_STRUCT.unpack_from(buf, offset)
            param_size = int.from_bytes(param_size, "little") * 4
            # Decode the param value.
            param_value_encoded = bytes(buf[offset+8:offset+param_size])  # 8 is the size of the param header.
            params[decode_identifier(param_name)] = decode_value(param_type_id, param_value_encoded)
            offset += param_size
            # Check for param overflow.
            if offset > param_limit:  # pragma: no cover
                raise DecodeError("Parameter overflow by {} bytes".format(offset - param_limit))
        # Store the field.
        fields.append(FieldData(decode_identifier(field_name), field_id, params))
    # Check for field overflow.
    if offset > field_limit:  # pragma: no cover
        raise DecodeError("Field overflow by {} bytes".format(offset - field_limit))
    # All done!
    return fields


# Packet encoding.

def encode_packet(packet_type, packet_id, timestamp, info, fields):
    timestamp_unix, timestamp_nano = datetime_to_unix(timestamp)
    # Encode the header.
    buf = bytearray(32)  # 32 is the size of the packet header.
    PACKET_HEADER_STRUCT.pack_into(
        buf, 0,
        b"\xdd\xcc\xbb\xaa",  # Hardcoded packet header.
        packet_type.encode("latin1"),
        0,  # Placeholder for the packet size, which we will calculate soon.
        packet_id,
        b'\x01\x00\x00\x00',
        timestamp_unix, timestamp_nano,
        info,
    )
    offset = 32  # 32 is the size of the packet header.
    # Write the packet fields.
    for field_name, field_id, params in fields:
        field_offset = offset
        # Write the field header.
        buf.extend(FIELD_HEADER_STRUCT.pack(
            field_name.encode("latin1"),
            b"\x00\x00\x00",  # Placeholder for the field size, which we will calculate soom.
            b"\x00",  # Field type ID is ignored.
            field_id,
        ))
        offset += 12  # 12 is the size of the field header.
        # Write the params.
        for param_name, param_value in params.items():
            # Encode the param value.
            param_type_id, param_encoded_value = encode_value(param_value)
            # Write the param header.
            param_size = 8 + len(param_encoded_value)  # 8 is the size of the param header.
            param_padding_size = -param_size % 4
            buf.extend(PARAM_HEADER_STRUCT.pack(
                param_name.encode("latin1"),
                ((param_size + param_padding_size) // 4).to_bytes(3, "little"),
                param_type_id,
            ))
            # Write the param value.
            buf.extend(param_encoded_value)
            buf.extend(b"\x00" * param_padding_size)
            offset += param_size + param_padding_size
        # Write the field size.
        buf[field_offset+4:field_offset+7] = ((offset - field_offset) // 4).to_bytes(3, "little")[:3]
    # Encode the packet footer.
    buf.extend(b"\x00\x00\x00\x00\xaa\xbb\xcc\xdd")  # Hardcoded packet footer with no checksum.
    # Write the packet size.
    buf[8:12] = ((offset + 8) // 4).to_bytes(4, "little")  # 8 is the size of the packet footer.
    # All done!
    return buf


# PacketData decoding.

PacketData = namedtuple("PacketData", ("type", "id", "timestamp", "info", "fields",))


def decode_packet_cps(header_buf):
    (
        header,
        packet_type,
        size_words,
        packet_id,
        format_id,
        time,
        nanotime,
        info,
    ) = PACKET_HEADER_STRUCT.unpack(header_buf)
    packet_type = decode_identifier(packet_type)
    size = size_words * 4
    if header != b"\xdd\xcc\xbb\xaa":  # pragma: no cover
        raise DecodeError("Invalid packet header {}".format(header))
    timestamp = unix_to_datetime(time, nanotime)
    # Decode the rest of the body data.
    size_remaining = size - PACKET_HEADER_STRUCT.size

    def decode_packet_body(body_buf):
        if len(body_buf) > size_remaining:  # pragma: no cover
            raise DecodeError("Packet body overflow by {} bytes".format(len(body_buf) - size_remaining))
        fields = decode_fields(body_buf, 0, size_remaining - PACKET_FOOTER_STRUCT.size)
        (
            checksum,
            footer,
        ) = PACKET_FOOTER_STRUCT.unpack_from(body_buf, size_remaining - PACKET_FOOTER_STRUCT.size)
        if footer != b"\xaa\xbb\xcc\xdd":  # pragma: no cover
            raise DecodeError("Invalid packet footer {}".format(footer))
        # All done!
        return PacketData(
            type=packet_type,
            id=packet_id,
            timestamp=timestamp,
            info=info,
            fields=fields,
        )

    # Return the number of bytes to read, and the function to finish decoding.
    return size_remaining, decode_packet_body


def decode_packet(buf):
    body_size, decode_packet_body = decode_packet_cps(buf[:PACKET_HEADER_STRUCT.size])
    return decode_packet_body(buf[PACKET_HEADER_STRUCT.size:])
