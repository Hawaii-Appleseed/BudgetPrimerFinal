"""QR codes (INDESIGN_PARITY.md 42), with no dependency.

Byte mode, error-correction level M (about 15% of the symbol can be damaged
and it still reads — enough for print), versions 1–10: up to 213 bytes, which
is any link a report prints. Pure Python because the editor runs this same
engine in the browser through Pyodide, where a compiled library is not there.

`matrix(data)` returns the symbol as rows of booleans (True = dark), without
the quiet zone; `svg_path(data)` the dark modules as one SVG path in module
units, the quiet zone included in its offset. The encoder follows ISO/IEC
18004: data codewords, Reed–Solomon error correction per block, interleaving,
function patterns, the eight masks scored by the four penalty rules, and the
format and version information. tests: test_docsync.py compares matrices
with an independent encoder for every version.
"""
from __future__ import annotations

# Level M: (EC codewords per block, [(blocks, data codewords per block), …])
_BLOCKS_M = {
    1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]),
    5: (24, [(2, 43)]), 6: (16, [(4, 27)]), 7: (18, [(4, 31)]),
    8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]),
    10: (26, [(4, 43), (1, 44)]),
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
          7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
MAX_BYTES = 213


class QRError(ValueError):
    pass


# --- Reed–Solomon over GF(256), polynomial 0x11D --------------------------------
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


def _rs_generator(degree: int) -> list:
    g = [1]
    for i in range(degree):
        g = [a ^ b for a, b in zip(g + [0], [0] + [_mul(c, _EXP[i]) for c in g])]
    return g


def _rs_remainder(data: list, degree: int) -> list:
    gen = _rs_generator(degree)
    rem = list(data) + [0] * degree
    for i in range(len(data)):
        c = rem[i]
        if c:
            for j in range(1, len(gen)):
                rem[i + j] ^= _mul(gen[j], c)
    return rem[len(data):]


# --- codewords --------------------------------------------------------------------

def _version_for(n: int) -> int:
    for v in range(1, 11):
        cap = sum(b * d for b, d in _BLOCKS_M[v][1])
        if 4 + (8 if v < 10 else 16) + 8 * n <= 8 * cap:
            return v
    raise QRError(f"{n} bytes is more than a QR code here carries ({MAX_BYTES})")


def _codewords(data: bytes, v: int) -> list:
    ec, groups = _BLOCKS_M[v]
    cap = sum(b * d for b, d in groups)
    bits = [0, 1, 0, 0]                                         # byte mode
    cc = 8 if v < 10 else 16
    bits += [(len(data) >> i) & 1 for i in range(cc - 1, -1, -1)]
    for byte in data:
        bits += [(byte >> i) & 1 for i in range(7, -1, -1)]
    bits += [0] * min(4, 8 * cap - len(bits))                   # terminator
    bits += [0] * (-len(bits) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    while len(words) < cap:
        words.append(pad[(len(words) - len(bits) // 8) % 2])
    blocks, i = [], 0
    for b, d in groups:
        for _ in range(b):
            blocks.append(words[i:i + d])
            i += d
    eccs = [_rs_remainder(bl, ec) for bl in blocks]
    out = []
    for k in range(max(len(bl) for bl in blocks)):
        out += [bl[k] for bl in blocks if k < len(bl)]
    for k in range(ec):
        out += [e[k] for e in eccs]
    return out


# --- the symbol ---------------------------------------------------------------------

def _bch(value: int, poly: int, bits: int) -> int:
    v = value << (poly.bit_length() - 1)
    while v.bit_length() >= poly.bit_length():
        v ^= poly << (v.bit_length() - poly.bit_length())
    return (value << (poly.bit_length() - 1)) | v


def _format_bits(mask: int) -> int:
    return _bch((0b00 << 3) | mask, 0b10100110111, 15) ^ 0b101010000010010   # level M = 00


def _base(v: int):
    n = 17 + 4 * v
    m = [[None] * n for _ in range(n)]
    fn = [[False] * n for _ in range(n)]

    def put(r, c, dark):
        m[r][c] = dark
        fn[r][c] = True

    for (r0, c0) in ((0, 0), (0, n - 7), (n - 7, 0)):           # finders + separators
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = r0 + r, c0 + c
                if 0 <= rr < n and 0 <= cc < n:
                    dark = (0 <= r <= 6 and 0 <= c <= 6
                            and (r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4)))
                    put(rr, cc, dark)
    for i in range(8, n - 8):                                   # timing
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)
    al = _ALIGN[v]
    last = len(al) - 1
    for i, r in enumerate(al):                                  # alignment
        for j, c in enumerate(al):
            # Skip only the three under the finders. One whose centre is on a
            # timing line (6, 22) is still drawn — skipping every centre that
            # was already a function module dropped those from version 7 up.
            if (i, j) in ((0, 0), (0, last), (last, 0)):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    put(r + dr, c + dc, max(abs(dr), abs(dc)) != 1)
    put(n - 8, 8, True)                                         # the dark module
    for i in range(9):                                          # reserve format areas
        if not fn[8][i]:
            put(8, i, False)
        if not fn[i][8]:
            put(i, 8, False)
    for i in range(8):
        put(8, n - 1 - i, False)
        put(n - 1 - i, 8, False)
    if v >= 7:                                                  # version information
        vb = _bch(v, 0b1111100100101, 18)
        for i in range(18):
            bit = (vb >> i) & 1 == 1
            a, b = n - 11 + i % 3, i // 3
            put(a, b, bit)
            put(b, a, bit)
    return m, fn


def _place(m, fn, words):
    n = len(m)
    bits = [(w >> (7 - i)) & 1 for w in words for i in range(8)]
    k, right = 0, n - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(n):
            for j in range(2):
                c = right - j
                upward = ((right + 1) & 2) == 0
                r = n - 1 - vert if upward else vert
                if not fn[r][c]:
                    m[r][c] = k < len(bits) and bits[k] == 1
                    k += 1
        right -= 2


_MASKS = [
    lambda r, c: (r + c) % 2 == 0, lambda r, c: r % 2 == 0, lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0, lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0, lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _apply(m, fn, mask):
    n = len(m)
    out = [row[:] for row in m]
    f = _MASKS[mask]
    for r in range(n):
        for c in range(n):
            if not fn[r][c] and f(r, c):
                out[r][c] = not out[r][c]
    fb = _format_bits(mask)
    for i in range(15):
        bit = (fb >> i) & 1 == 1
        # first copy, around the top-left finder
        if i < 6:
            out[i][8] = bit
        elif i < 8:
            out[i + 1][8] = bit
        else:
            out[8][14 - i if i > 8 else 7] = bit
        # second copy, split between the other two finders
        if i < 8:
            out[8][n - 1 - i] = bit
        else:
            out[n - 15 + i][8] = bit
    out[n - 8][8] = True
    return out


def _penalty(m) -> int:
    n, p = len(m), 0
    for line in list(m) + [list(col) for col in zip(*m)]:
        run, prev = 0, None
        for x in line:
            if x == prev:
                run += 1
            else:
                if run >= 5:
                    p += run - 2
                run, prev = 1, x
        if run >= 5:
            p += run - 2
        s = "".join("1" if x else "0" for x in line)
        for pat in ("10111010000", "00001011101"):
            i = s.find(pat)
            while i != -1:
                p += 40
                i = s.find(pat, i + 1)
    for r in range(n - 1):
        for c in range(n - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                p += 3
    dark = sum(x for row in m for x in row)
    p += abs(dark * 20 - n * n * 10) // (n * n) * 10
    return p


def matrix(data: str | bytes, mask: int | None = None) -> list:
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    if not raw:
        raise QRError("a QR code needs something to say")
    v = _version_for(len(raw))
    m, fn = _base(v)
    _place(m, fn, _codewords(raw, v))
    if mask is not None:
        return _apply(m, fn, mask)
    best = min(range(8), key=lambda k: _penalty(_apply(m, fn, k)))
    return _apply(m, fn, best)


def svg_path(data: str | bytes, quiet: int = 4) -> tuple[str, int]:
    """(path data of the dark modules in module units, side incl. quiet zone)."""
    m = matrix(data)
    n = len(m)
    parts = []
    for r, row in enumerate(m):
        c = 0
        while c < n:
            if row[c]:
                start = c
                while c < n and row[c]:
                    c += 1
                parts.append(f"M{start + quiet} {r + quiet}h{c - start}v1h-{c - start}z")
            else:
                c += 1
    return "".join(parts), n + 2 * quiet
