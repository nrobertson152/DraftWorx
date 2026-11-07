# SPDX-License-Identifier: GPL-3.0-or-later
"""QR codes for FieldWorx labels: one standard-library file, Python 3.9 or newer, no other imports.

This is the reference copy, in FieldWorx (bridge/fieldworx_bridge/fwx_label.py). Programs that print FieldWorx codes
copy it unchanged, as integrations copy RevisionWorx's rwx_client.py, so every program draws the same code for the same
text without importing another. Keep the FWX_LABEL_VERSION line so copies can be compared.

    from fwx_label import encode, label_code
    rows = encode(label_code("R", "SCH-12@B"), "Q")    # rows of booleans, True = dark; no quiet zone

What it draws: QR Code model 2 (ISO/IEC 18004:2015), versions 1 to 10 (up to 271 bytes at level L), the text as UTF-8 in
byte mode without an ECI header, the smallest version that holds it and the mask with the lowest penalty score. Leave a
quiet zone of at least 4 modules around it. FieldWorx's tests check every version, level and mask bit for bit against an
independent encoder (python-qrcode) and read the codes back with zxing-cpp, the decoder the FieldWorx app uses.

What a label holds is FieldWorx's docs/PROTOCOL.md section 7: ``FWX:<kind>:<code>``.
"""
from __future__ import annotations

__all__ = ["encode", "label_code", "LEVELS", "KINDS", "QRError", "FWX_LABEL_VERSION"]

FWX_LABEL_VERSION = 1
KINDS = ("A", "P", "D", "J", "R")     # asset, part, document, project, revision (docs/PROTOCOL.md section 7)

LEVELS = ("L", "M", "Q", "H")
_FORMAT_BITS = {"L": 1, "M": 0, "Q": 3, "H": 2}

# Error correction per version and level: (codewords per block, [(blocks, data codewords per block), ...]).
_EC = {
    1: {"L": (7, [(1, 19)]), "M": (10, [(1, 16)]), "Q": (13, [(1, 13)]), "H": (17, [(1, 9)])},
    2: {"L": (10, [(1, 34)]), "M": (16, [(1, 28)]), "Q": (22, [(1, 22)]), "H": (28, [(1, 16)])},
    3: {"L": (15, [(1, 55)]), "M": (26, [(1, 44)]), "Q": (18, [(2, 17)]), "H": (22, [(2, 13)])},
    4: {"L": (20, [(1, 80)]), "M": (18, [(2, 32)]), "Q": (26, [(2, 24)]), "H": (16, [(4, 9)])},
    5: {"L": (26, [(1, 108)]), "M": (24, [(2, 43)]), "Q": (18, [(2, 15), (2, 16)]), "H": (22, [(2, 11), (2, 12)])},
    6: {"L": (18, [(2, 68)]), "M": (16, [(4, 27)]), "Q": (24, [(4, 19)]), "H": (28, [(4, 15)])},
    7: {"L": (20, [(2, 78)]), "M": (18, [(4, 31)]), "Q": (18, [(2, 14), (4, 15)]), "H": (26, [(4, 13), (1, 14)])},
    8: {"L": (24, [(2, 97)]), "M": (22, [(2, 38), (2, 39)]), "Q": (22, [(4, 18), (2, 19)]), "H": (26, [(4, 14), (2, 15)])},
    9: {"L": (30, [(2, 116)]), "M": (22, [(3, 36), (2, 37)]), "Q": (20, [(4, 16), (4, 17)]), "H": (24, [(4, 12), (4, 13)])},
    10: {"L": (18, [(2, 68), (2, 69)]), "M": (26, [(4, 43), (1, 44)]), "Q": (24, [(6, 19), (2, 20)]),
         "H": (28, [(6, 15), (2, 16)])},
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
          9: [6, 26, 46], 10: [6, 28, 50]}
MAX_VERSION = max(_EC)


class QRError(ValueError):
    """The text doesn't fit a QR code this file draws (versions 1 to 10)."""


# --------------------------------------------------------------------------------------------- Reed-Solomon
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _generator(n: int) -> list[int]:
    g = [1]
    for i in range(n):
        g = [a ^ _mul(b, _EXP[i]) for a, b in zip(g + [0], [0] + g)]
    return g


def _ec_codewords(data: list[int], n: int) -> list[int]:
    gen = _generator(n)                      # highest power first, gen[0] == 1
    rem = list(data) + [0] * n
    for i in range(len(data)):
        c = rem[i]
        if c:
            for j in range(1, len(gen)):
                rem[i + j] ^= _mul(gen[j], c)
    return rem[len(data):]


# ------------------------------------------------------------------------------------------------- data
def _capacity(version: int, level: str) -> int:
    return sum(b * d for b, d in _EC[version][level][1])


def _data_codewords(payload: bytes, version: int, level: str) -> list[int]:
    count_bits = 8 if version <= 9 else 16
    bits: list[int] = []

    def put(value: int, n: int) -> None:
        bits.extend((value >> (n - 1 - i)) & 1 for i in range(n))

    put(0b0100, 4)
    put(len(payload), count_bits)
    for byte in payload:
        put(byte, 8)
    total = _capacity(version, level) * 8
    put(0, min(4, total - len(bits)))
    bits.extend([0] * (-len(bits) % 8))
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    while len(words) < total // 8:
        words.append(pad[(len(words) - len(bits) // 8) % 2])
    return words


def _interleave(words: list[int], version: int, level: str) -> list[int]:
    ec_len, groups = _EC[version][level]
    blocks, pos = [], 0
    for count, size in groups:
        for _ in range(count):
            blocks.append(words[pos:pos + size])
            pos += size
    ecs = [_ec_codewords(b, ec_len) for b in blocks]
    out = [b[i] for i in range(max(len(b) for b in blocks)) for b in blocks if i < len(b)]
    return out + [e[i] for i in range(ec_len) for e in ecs]


# ------------------------------------------------------------------------------------------------- matrix
def _bch(value: int, poly: int, poly_bits: int) -> int:
    v = value << (poly_bits - 1)
    while v.bit_length() >= poly_bits:
        v ^= poly << (v.bit_length() - poly_bits)
    return v


def _format_bits(level: str, mask: int) -> int:
    data = (_FORMAT_BITS[level] << 3) | mask
    return ((data << 10) | _bch(data, 0b10100110111, 11)) ^ 0b101010000010010


def _version_bits(version: int) -> int:
    return (version << 12) | _bch(version, 0b1111100100101, 13)


def _base(version: int) -> tuple[list[list[bool | None]], list[list[bool]]]:
    """The function patterns, and which modules they occupy."""
    n = 17 + 4 * version
    m: list[list[bool | None]] = [[None] * n for _ in range(n)]
    fixed = [[False] * n for _ in range(n)]

    def set_(r: int, c: int, v: bool) -> None:
        m[r][c] = v
        fixed[r][c] = True

    for r0, c0 in ((0, 0), (0, n - 7), (n - 7, 0)):
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = r0 + r, c0 + c
                if 0 <= rr < n and 0 <= cc < n:
                    ring = max(abs(r - 3), abs(c - 3))
                    set_(rr, cc, ring != 2 and ring != 4)
    centres = _ALIGN[version]
    for r in centres:                                     # alignment patterns, except where a finder is
        for c in centres:
            if fixed[r][c]:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    set_(r + dr, c + dc, max(abs(dr), abs(dc)) != 1)
    for i in range(8, n - 8):                             # timing patterns, which run through alignment patterns
        if not fixed[6][i]:
            set_(6, i, i % 2 == 0)
        if not fixed[i][6]:
            set_(i, 6, i % 2 == 0)
    for i in range(9):                                    # format areas, written later
        if not fixed[8][i]:
            set_(8, i, False)
        if not fixed[i][8]:
            set_(i, 8, False)
    for i in range(8):
        set_(8, n - 1 - i, False)
    for i in range(7):
        set_(n - 1 - i, 8, False)
    set_(n - 8, 8, True)                                  # the dark module
    if version >= 7:
        for i in range(18):
            set_(n - 11 + i % 3, i // 3, False)
            set_(i // 3, n - 11 + i % 3, False)
    return m, fixed


_MASKS = (lambda r, c: (r + c) % 2 == 0, lambda r, c: r % 2 == 0, lambda r, c: c % 3 == 0,
          lambda r, c: (r + c) % 3 == 0, lambda r, c: (r // 2 + c // 3) % 2 == 0,
          lambda r, c: (r * c) % 2 + (r * c) % 3 == 0, lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
          lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0)


def _place(m: list[list[bool | None]], fixed: list[list[bool]], codewords: list[int]) -> None:
    n = len(m)
    bits = [(w >> (7 - i)) & 1 for w in codewords for i in range(8)]
    k, upward, col = 0, True, n - 1
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(n - 1, -1, -1) if upward else range(n)
        for r in rows:
            for c in (col, col - 1):
                if not fixed[r][c]:
                    m[r][c] = bool(bits[k]) if k < len(bits) else False
                    k += 1
        upward = not upward
        col -= 2


def _write_format(m: list[list[bool | None]], version: int, level: str, mask: int) -> None:
    n = len(m)
    f = _format_bits(level, mask)
    bit = [(f >> i) & 1 == 1 for i in range(15)]       # bit 0 is the least significant
    for i in range(6):
        m[8][i] = bit[14 - i]
    m[8][7], m[8][8], m[7][8] = bit[8], bit[7], bit[6]
    for i in range(9, 15):
        m[14 - i][8] = bit[14 - i]
    for i in range(8):                                    # second copy: bits 0-7 along row 8, 8-14 up column 8
        m[8][n - 1 - i] = bit[i]
    for i in range(8, 15):
        m[n - 15 + i][8] = bit[i]
    if version >= 7:
        v = _version_bits(version)
        for i in range(18):
            b = (v >> i) & 1 == 1
            m[n - 11 + i % 3][i // 3] = b
            m[i // 3][n - 11 + i % 3] = b


def _penalty(m: list[list[bool]]) -> int:
    n = len(m)
    score = 0
    lines = m + [list(col) for col in zip(*m)]
    for line in lines:                                    # N1: runs of five or more
        run, prev = 0, None
        for v in line:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run, prev = 1, v
        if run >= 5:
            score += run - 2
    for r in range(n - 1):                                # N2: 2 x 2 blocks
        for c in range(n - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    pattern_a = [True, False, True, True, True, False, True, False, False, False, False]
    pattern_b = pattern_a[::-1]
    for line in lines:                                    # N3: finder-like patterns
        for i in range(n - 10):
            window = line[i:i + 11]
            if window == pattern_a or window == pattern_b:
                score += 40
    dark = sum(v for row in m for v in row)               # N4: balance
    score += 10 * (abs(dark * 20 - n * n * 10) // (n * n))
    return score


def encode(text: str, level: str = "M", version: int | None = None, mask: int | None = None) -> list[list[bool]]:
    """The QR code of ``text`` (UTF-8, byte mode) at error correction ``level`` (L, M, Q or H: 7, 15, 25 or 30% of the
    codewords recoverable): rows of booleans, True = dark, without the quiet zone. The smallest version that holds the
    text and the mask with the lowest penalty score, unless ``version`` (1 to 10) or ``mask`` (0 to 7) is given. Raises
    QRError if the text doesn't fit."""
    if level not in LEVELS:
        raise QRError(f"level is one of {', '.join(LEVELS)}, not {level!r}")
    if mask is not None and mask not in range(8):
        raise QRError(f"mask is 0 to 7, not {mask!r}")
    if version is not None and version not in _EC:
        raise QRError(f"version is 1 to {MAX_VERSION}, not {version!r}")
    payload = text.encode("utf-8")
    for v in ([version] if version is not None else list(range(1, MAX_VERSION + 1))):
        if 4 + (8 if v <= 9 else 16) + 8 * len(payload) <= 8 * _capacity(v, level):
            break
    else:
        largest = version if version is not None else MAX_VERSION
        raise QRError(f"{len(payload)} bytes don't fit a version {largest} QR code at level {level}")
    codewords = _interleave(_data_codewords(payload, v, level), v, level)
    base, fixed = _base(v)
    best: tuple[int, list[list[bool]]] | None = None
    for k in ([mask] if mask is not None else range(8)):
        m = [row[:] for row in base]
        _place(m, fixed, codewords)
        test = _MASKS[k]
        for r in range(len(m)):
            for c in range(len(m)):
                if not fixed[r][c] and test(r, c):
                    m[r][c] = not m[r][c]
        _write_format(m, v, level, k)
        done = [[bool(x) for x in row] for row in m]
        p = _penalty(done) if mask is None else 0
        if best is None or p < best[0]:
            best = (p, done)
    return best[1]


def label_code(kind: str, code: str) -> str:
    """The text a FieldWorx label holds (docs/PROTOCOL.md section 7): ``FWX:<kind>:<code>``. For a revision, ``code`` is
    ``<document number>@<revision>``."""
    if kind not in KINDS:
        raise QRError(f"a label's kind is one of {', '.join(KINDS)}, not {kind!r}")
    if not code.strip():
        raise QRError("a label's code is empty")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in code):
        raise QRError(f"a label's code has a control character: {code!r}")
    if kind == "R" and not _revision_code(code):
        raise QRError(f"a revision label's code is DOCUMENT@REVISION, not {code!r}")
    return f"FWX:{kind}:{code}"


def _revision_code(code: str) -> bool:
    number, at, revision = code.rpartition("@")
    return bool(at and number.strip() and revision.strip())
