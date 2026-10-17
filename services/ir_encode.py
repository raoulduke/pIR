"""Protocol encoder for the Fun menu's code library (data/fun_codes.json,
built by scripts/build_fun_codes.py from Flipper-IRDB).

Turns a library entry into the raw pulse/space timings ir-ctl sends, plus
the carrier that protocol needs — Learn captures are already raw timings at
the 38 kHz default, but Sony (40 kHz), Philips RC5/RC6 (36 kHz) and
Panasonic (~37 kHz) codes need their own carrier.

Entries are either
  {"protocol": "NECext", "address": 0x0586, "command": 0xF00F, ["carrier": Hz]}
or
  {"raw": [mark, space, mark, ...], "carrier": Hz}
with address/command in Flipper's representation — bit layouts and timings
follow Flipper's own encoders (flipperzero-firmware lib/infrared/
encoder_decoder), so a code works here the way it does on a Flipper.

Each protocol sends a minimal "short press": enough frames/repeats that a
TV registers it once (one NEC repeat code, three Sony frames, ...).
"""

DEFAULT_CARRIER = 38000

_CARRIERS = {
    "SIRC": 40000, "SIRC15": 40000, "SIRC20": 40000,
    "RC5": 36000, "RC5X": 36000, "RC6": 36000,
    "Kaseikyo": 37000,
}

# LCD names, short enough that "#10 NECx 7F00:0A" fits on 16 characters.
_ABBREVIATIONS = {
    "NECext": "NECx", "Samsung32": "Sam32", "Samsung36": "Sam36",
    "Kaseikyo": "Kasei",
}


def _bits_lsb(value: int, count: int) -> list[int]:
    return [(value >> i) & 1 for i in range(count)]


def _bits_msb(value: int, count: int) -> list[int]:
    return [(value >> (count - 1 - i)) & 1 for i in range(count)]


def _pulse_distance(bits: list[int], mark: int, space0: int, space1: int) -> list[int]:
    """mark + (short | long) space per bit, then a stop mark."""
    out = []
    for bit in bits:
        out += [mark, space1 if bit else space0]
    return out + [mark]


def _pulse_width(bits: list[int], mark0: int, mark1: int, space: int) -> list[int]:
    """(short | long) mark per bit, spaces between — no trailing space."""
    out = []
    for bit in bits:
        out += [mark1 if bit else mark0, space]
    return out[:-1]


def _manchester(halves: list[tuple[int, int]]) -> list[int]:
    """(level, duration) half-bits -> alternating mark/space durations,
    merging same-level neighbours and dropping leading/trailing space."""
    out: list[list[int]] = []
    for level, duration in halves:
        if out and out[-1][0] == level:
            out[-1][1] += duration
        else:
            out.append([level, duration])
    while out and out[0][0] == 0:
        out.pop(0)
    while out and out[-1][0] == 0:
        out.pop()
    return [d for _, d in out]


def _repeat(frame: list[int], count: int, period: int) -> list[int]:
    """*count* copies of *frame*, each padded with a space to *period* µs."""
    gap = max(period - sum(frame), 5000)
    out = []
    for i in range(count):
        if i:
            out.append(gap)
        out += frame
    return out


def _nec(address: int, command: int, extended: bool) -> list[int]:
    if extended:
        data = (address & 0xFFFF) | ((command & 0xFFFF) << 16)
    else:
        a, c = address & 0xFF, command & 0xFF
        data = a | ((~a & 0xFF) << 8) | (c << 16) | ((~c & 0xFF) << 24)
    frame = [9000, 4500] + _pulse_distance(_bits_lsb(data, 32), 560, 560, 1690)
    repeat_code = [9000, 2250, 560]
    return frame + [110000 - sum(frame)] + repeat_code


def _samsung32(address: int, command: int) -> list[int]:
    a, c = address & 0xFF, command & 0xFF
    data = a | (a << 8) | (c << 16) | ((~c & 0xFF) << 24)
    frame = [4500, 4500] + _pulse_distance(_bits_lsb(data, 32), 550, 550, 1650)
    return _repeat(frame, 2, 108000)


def _samsung36(address: int, command: int) -> list[int]:
    """16 address bits, a 500/4500 separator, then 20 command bits (all
    LSB first) — the layout of this project's own cottage DVD capture
    (signals/cottage/dvd/power.txt). Not in Flipper-IRDB."""
    first = _pulse_distance(_bits_lsb(address, 16), 500, 500, 1500)
    second = _pulse_distance(_bits_lsb(command, 20), 500, 500, 1500)
    frame = [4500, 4500] + first + [4500] + second
    return _repeat(frame, 2, sum(frame) + 25500)


def _sirc(address: int, command: int, address_bits: int) -> list[int]:
    bits = _bits_lsb(command, 7) + _bits_lsb(address, address_bits)
    frame = [2400, 600] + _pulse_width(bits, 600, 1200, 600)
    return _repeat(frame, 3, 45000)


def _rc5(address: int, command: int, extended: bool) -> list[int]:
    # S1, S2 (RC5X: command bit 6, inverted — Flipper's RC5X command is
    # the low 6 bits with bit 6 implied set), toggle 0, address, command.
    bits = [1, 0 if extended else 1, 0] + _bits_msb(address, 5) + _bits_msb(command, 6)
    t = 889
    halves = []
    for bit in bits:  # RC5: 1 = space then mark, 0 = mark then space
        halves += [(0, t), (1, t)] if bit else [(1, t), (0, t)]
    return _repeat(_manchester(halves), 2, 113778)


def _rc6(address: int, command: int) -> list[int]:
    t = 444
    halves = [(1, 6 * t), (0, 2 * t)]           # leader
    bits = [(1, t), (0, t), (0, t), (0, t), (0, 2 * t)]  # start, mode 000, toggle (double width)
    bits += [(b, t) for b in _bits_msb(address, 8) + _bits_msb(command, 8)]
    for bit, width in bits:  # RC6: 1 = mark then space, 0 = space then mark
        halves += [(1, width), (0, width)] if bit else [(0, width), (1, width)]
    return _repeat(_manchester(halves), 2, 107000)


def _kaseikyo(address: int, command: int) -> list[int]:
    vendor = (address >> 8) & 0xFFFF
    genre1, genre2, ident = (address >> 4) & 0xF, address & 0xF, (address >> 24) & 3
    data = [vendor & 0xFF, vendor >> 8]
    parity = data[0] ^ data[1]
    data.append(((parity & 0xF) ^ (parity >> 4)) | (genre1 << 4))
    data.append((genre2 & 0xF) | ((command & 0xF) << 4))
    data.append((ident << 6) | ((command >> 4) & 0x3F))
    data.append(data[2] ^ data[3] ^ data[4])
    bits = [b for byte in data for b in _bits_lsb(byte, 8)]
    unit = 432
    frame = [8 * unit, 4 * unit] + _pulse_distance(bits, unit, unit, 3 * unit)
    return _repeat(frame, 2, 130000)


def _rca(address: int, command: int) -> list[int]:
    a, c = address & 0xF, command & 0xFF
    data = a | (c << 4) | ((~a & 0xF) << 12) | ((~c & 0xFF) << 16)
    frame = [4000, 4000] + _pulse_distance(_bits_lsb(data, 24), 500, 1000, 2000)
    return _repeat(frame, 2, sum(frame) + 8000)


def _sharp(address: int, command: int) -> list[int]:
    """Frame, then the same frame with command/expansion/check inverted."""
    def frame(c: int, tail: list[int]) -> list[int]:
        bits = _bits_lsb(address, 5) + _bits_lsb(c, 8) + tail
        return _pulse_distance(bits, 320, 680, 1680)
    first = frame(command & 0xFF, [1, 0])
    second = frame(~command & 0xFF, [0, 1])
    return first + [40000] + second


def _jvc(address: int, command: int) -> list[int]:
    bits = _bits_lsb(address, 8) + _bits_lsb(command, 8)
    body = _pulse_distance(bits, 526, 526, 1578)
    frame = [8400, 4200] + body
    return frame + [55000 - sum(frame)] + body  # repeat = headerless frame


def encode(entry: dict) -> tuple[list[int], int]:
    """Library entry -> (alternating mark/space µs starting with a mark, carrier Hz)."""
    if "raw" in entry:
        pulses = [int(v) for v in entry["raw"]]
        if len(pulses) % 2 == 0:
            pulses = pulses[:-1]  # ends on a mark
        return pulses, int(entry.get("carrier", DEFAULT_CARRIER))

    protocol, a, c = entry["protocol"], int(entry["address"]), int(entry["command"])
    encoders = {
        "NEC": lambda: _nec(a, c, False),
        "NECext": lambda: _nec(a, c, True),
        "Samsung32": lambda: _samsung32(a, c),
        "Samsung36": lambda: _samsung36(a, c),
        "SIRC": lambda: _sirc(a, c, 5),
        "SIRC15": lambda: _sirc(a, c, 8),
        "SIRC20": lambda: _sirc(a, c, 13),
        "RC5": lambda: _rc5(a, c, False),
        "RC5X": lambda: _rc5(a, c, True),
        "RC6": lambda: _rc6(a, c),
        "Kaseikyo": lambda: _kaseikyo(a, c),
        "RCA": lambda: _rca(a, c),
        "Sharp": lambda: _sharp(a, c),
        "JVC": lambda: _jvc(a, c),
    }
    if protocol not in encoders:
        raise ValueError(f"Unsupported IR protocol: {protocol}")
    carrier = int(entry.get("carrier", _CARRIERS.get(protocol, DEFAULT_CARRIER)))
    return encoders[protocol](), carrier


def to_signal_text(pulses: list[int], carrier: int) -> str:
    """ir-ctl's +pulse -space text, with a "carrier N" first line when it
    isn't the 38 kHz default — hardware/ir_transmitter.transmit() strips
    that line and passes it as ir-ctl -c, since ir-ctl itself rejects a
    carrier line mixed with +/- values."""
    values = [f"{'+' if i % 2 == 0 else '-'}{v}" for i, v in enumerate(pulses)]
    lines = [" ".join(values[i:i + 16]) for i in range(0, len(values), 16)]
    header = [] if carrier == DEFAULT_CARRIER else [f"carrier {carrier}"]
    return "\n".join(header + lines) + "\n"


def _hex(value: int, digits: int) -> str:
    return f"{value:0{digits}X}"


def label(entry: dict, number: int) -> str:
    """Short LCD label: "#3 NECx 0586:0F". Raw codes: "#5 Raw 38kHz"."""
    if "raw" in entry:
        return f"#{number} Raw {int(entry.get('carrier', DEFAULT_CARRIER)) // 1000}kHz"

    protocol, a, c = entry["protocol"], int(entry["address"]), int(entry["command"])
    if protocol == "NECext":
        address = _hex(a, 4)
        # Most NECext codes still invert the command byte — show just the
        # command then, like NEC; a genuine 16-bit command shows in full.
        command = _hex(c & 0xFF, 2) if (c >> 8) == (~c & 0xFF) else _hex(c, 4)
    elif protocol == "Kaseikyo":
        address, command = _hex(a & 0xFF, 2), _hex(c, 3)  # vendor ID omitted
    elif protocol == "RCA":
        address, command = _hex(a, 1), _hex(c, 2)
    elif protocol in ("Samsung36", "SIRC20"):
        address, command = _hex(a, 4 if protocol == "Samsung36" else 2), _hex(c, 2)
    else:
        address, command = _hex(a, 2), _hex(c, 2)

    name = _ABBREVIATIONS.get(protocol, protocol)
    text = f"#{number} {name} {address}:{command}"
    if "carrier" in entry:
        text += f" {int(entry['carrier']) // 1000}k"
    return text[:16]
