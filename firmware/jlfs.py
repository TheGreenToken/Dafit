#!/usr/bin/env python3
"""
jlfs.py — Reader/Writer für JieLi-Flash-Images (JLFS2).

Format nach kagaimiq (https://kagaimiq.github.io/jielie/datafmt/jlfs.html
und .../newfw.html) sowie eigener Verifikation:

  * CRC16 = CRC-16/XMODEM (Poly 0x1021, Init 0x0000, keine Reflexion)
    -> empirisch bestätigt an den drei dokumentierten tone.idx-Einträgen,
       siehe test_jlfs.py::test_crc_matches_documented_tone_idx

Layout eines Flash-Images:

    +0x0000  Flash-Header (32 B)
    +0x0020  Datei-Header #0 (32 B)
             Datei-Header #1
             ...
             Datei-Header #n
             <Padding auf Blockgrenze>
             Daten #0
             Daten #1
             ...
             Daten #n

Ein Verzeichnis-Eintrag (z. B. "app_dir_head") zeigt auf einen eigenen Block,
der wiederum [Header-Tabelle][Daten] enthält. Dessen Offsets sind relativ zur
Position des Verzeichnis-Headers.

Nutzung:
    python3 jlfs.py info      image.bin
    python3 jlfs.py list      image.bin
    python3 jlfs.py extract   image.bin -o dump/
    python3 jlfs.py patch     image.bin --name app.bin --file new_app.bin -o patched.bin
    python3 jlfs.py verify    image.bin

Hinweis: Verschlüsselte/"gescrambelte" Images (so wie sie roh aus dem Flash
kommen) müssen vorher descrambelt werden; dieses Tool erwartet ein lesbares
Image. Siehe README.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
from dataclasses import dataclass, field
from typing import List, Optional

FLASH_HDR_SIZE = 32
ENTRY_SIZE = 32
UNDEFINED = 0xFFFFFFFF

ATTR_TYPE_MASK = 0x0F
ATTR_TYPE_UBOOT = 0
ATTR_TYPE_DIR = 1
ATTR_TYPE_FILE = 2
ATTR_DIRECTORY = 3
ATTR_SPECIAL = 0x10
ATTR_UBOOT_LZ4 = 0x40


# --------------------------------------------------------------------- CRC

def crc16(data: bytes) -> int:
    """CRC-16/XMODEM — der CRC, den JieLi überall verwendet."""
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# ------------------------------------------------------------------ Modelle

@dataclass
class FlashHeader:
    burner_size: int = 0
    vid: bytes = b"\x00" * 4
    flash_size: int = 0
    fs_ver: int = 0
    block_align: int = 1
    resvd: int = 0
    special_opt: int = 0
    pid: bytes = b"\x00" * 16

    @classmethod
    def parse(cls, buf: bytes, off: int = 0) -> "FlashHeader":
        raw = buf[off:off + FLASH_HDR_SIZE]
        if len(raw) < FLASH_HDR_SIZE:
            raise ValueError("Flash-Header abgeschnitten")
        stored = struct.unpack_from("<H", raw, 0)[0]
        calc = crc16(raw[2:])
        if stored != calc:
            raise ValueError(
                f"Flash-Header-CRC falsch (gespeichert 0x{stored:04X}, "
                f"berechnet 0x{calc:04X}) — falscher Offset oder Image gescrambelt"
            )
        flash_size = struct.unpack_from("<I", raw, 8)[0]
        pid_raw = raw[16:32]
        # Ein reiner Nullblock hat ebenfalls CRC 0 — daher Plausibilität prüfen.
        if not (0x10000 <= flash_size <= 0x40000000):
            raise ValueError(f"unplausible Flash-Größe 0x{flash_size:X}")
        if pid_raw.rstrip(b"\x00") and not all(32 <= c < 127 for c in pid_raw.rstrip(b"\x00")):
            raise ValueError("PID ist nicht druckbar")
        h = cls()
        h.burner_size = struct.unpack_from("<H", raw, 2)[0]
        h.vid = raw[4:8]
        h.flash_size = struct.unpack_from("<I", raw, 8)[0]
        h.fs_ver = raw[12]
        h.block_align = raw[13]
        h.resvd = raw[14]
        h.special_opt = raw[15]
        h.pid = raw[16:32]
        return h

    def build(self) -> bytes:
        body = struct.pack("<H", self.burner_size)
        body += self.vid[:4].ljust(4, b"\x00")
        body += struct.pack("<I", self.flash_size)
        body += bytes([self.fs_ver, self.block_align, self.resvd, self.special_opt])
        body += self.pid[:16].ljust(16, b"\x00")
        crc = crc16(body)
        return struct.pack("<H", crc) + body

    @property
    def align_bytes(self) -> int:
        return max(1, self.block_align) * 256


@dataclass
class Entry:
    name: str
    attr: int
    index: int = 0
    data_crc: int = 0xFFFF
    offset_field: int = 0
    size_field: int = 0
    data: bytes = b""
    children: List["Entry"] = field(default_factory=list)
    hdr_pos: int = 0          # absolute Position des Headers im Image
    base: int = 0             # Bezugspunkt der Offset-Felder
    is_dir: bool = False

    @property
    def data_pos(self) -> int:
        """Absolute Position der Nutzdaten im Image."""
        return self.base + self.offset_field

    @property
    def file_type(self) -> int:
        return self.attr & ATTR_TYPE_MASK

    def describe(self) -> str:
        kind = "DIR " if self.is_dir else "FILE"
        return (f"{kind} {self.size_field:>10} B  attr=0x{self.attr:02X}  "
                f"crc=0x{self.data_crc:04X}  {self.name}")

    def walk(self, prefix: str = ""):
        path = f"{prefix}{self.name}"
        yield self, path
        for c in self.children:
            yield from c.walk(path + "/")


def _walk_entries(entries, prefix: str = ""):
    for e in entries:
        path = f"{prefix}{e.name}"
        yield e, path
        if e.is_dir:
            yield from _walk_entries(e.children, path + "/")

# ---------------------------------------------------------------- Parsing

class Image:
    def __init__(self, data: bytes):
        self.raw = data
        self.header = self._find_header(data)
        self.entries = self._parse_list(data, base=0, start=self._list_start)

    @property
    def _list_start(self) -> int:
        return self._hdr_offset + FLASH_HDR_SIZE

    def _find_header(self, data: bytes) -> FlashHeader:
        last_err: Optional[Exception] = None
        for off in (0x0000, 0x1000):
            if off + FLASH_HDR_SIZE > len(data):
                continue
            try:
                h = FlashHeader.parse(data, off)
                self._hdr_offset = off
                return h
            except ValueError as e:
                last_err = e
        raise ValueError(f"Kein gültiger Flash-Header gefunden: {last_err}")

    # -- Liste einlesen ---------------------------------------------------

    def _parse_list(self, buf: bytes, base: int, start: int) -> List[Entry]:
        entries: List[Entry] = []
        pos = start
        while pos + ENTRY_SIZE <= len(buf):
            raw = buf[pos:pos + ENTRY_SIZE]
            stored = struct.unpack_from("<H", raw, 0)[0]
            if crc16(raw[2:]) != stored:
                break
            name_bytes = raw[16:32].split(b"\x00", 1)[0]
            if not name_bytes:
                break
            name = name_bytes.decode("utf-8", "replace")

            e = Entry(
                name=name,
                attr=raw[12],
                index=struct.unpack_from("<H", raw, 14)[0],
                data_crc=struct.unpack_from("<H", raw, 2)[0],
                offset_field=struct.unpack_from("<I", raw, 4)[0],
                size_field=struct.unpack_from("<I", raw, 8)[0],
                hdr_pos=pos,
                base=base,
            )
            abs_off = base + e.offset_field

            # Verzeichnis? -> rekursiv (Kinder liegen ab Header-Ende)
            if (e.size_field == UNDEFINED
                    or e.file_type in (ATTR_TYPE_DIR, ATTR_DIRECTORY)):
                try:
                    kids = self._parse_list(buf, base=pos, start=abs_off)
                except Exception:
                    kids = []
                if kids:
                    e.children = kids
                    e.is_dir = True
                else:
                    e.data = buf[abs_off:abs_off + (0 if e.size_field == UNDEFINED
                                                    else e.size_field)]
            else:
                end = abs_off + e.size_field
                if e.size_field and end <= len(buf):
                    e.data = buf[abs_off:end]

            entries.append(e)
            pos += ENTRY_SIZE

        # Ist der size-Feld-Wert 0xFFFFFFFF bei einem Blatt, greift die
        # Heuristik oben nicht — dann wenigstens die Rohdaten retten.
        return entries

    # -- Ausgabe ----------------------------------------------------------

    def list_text(self) -> str:
        lines = []
        lines.append(f"Flash-Header @ 0x{self._hdr_offset:04X}")
        lines.append(f"  PID          : {self.header.pid.rstrip(chr(0).encode()).decode('utf-8','replace')}")
        h = self.header
        lines.append(f"  VID          : {h.vid!r}")
        lines.append(f"  Flash-Größe  : {h.flash_size} Bytes ({h.flash_size/1024/1024:.1f} MiB)")
        lines.append(f"  FS-Version   : {h.fs_ver}")
        lines.append(f"  Block-Align  : {h.align_bytes} B")
        lines.append("")
        for e, path in _walk_entries(self.entries):
            lines.append("  " * path.count("/") + e.describe())
        return "\n".join(lines)

    def root(self) -> Entry:
        r = Entry(name="", attr=ATTR_DIRECTORY, is_dir=True, children=self.entries)
        return r

    def verify(self) -> List[str]:
        problems = []
        for e, path in _walk_entries(self.entries):
            if e.is_dir:
                continue
            if e.data_crc == 0xFFFF or not e.data:
                continue
            calc = crc16(e.data)
            if calc != e.data_crc:
                problems.append(f"{path}: CRC 0x{e.data_crc:04X} != 0x{calc:04X}")
        return problems

    def extract(self, out_dir: str) -> None:
        for e, path in _walk_entries(self.entries):
            if e.is_dir:
                continue
            dest = os.path.join(out_dir, path)
            os.makedirs(os.path.dirname(dest) or out_dir, exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(e.data)


# ---------------------------------------------------------------- Schreiben

def _align(n: int, a: int) -> int:
    return (n + a - 1) // a * a


def _build_list(entries: List[Entry], start_offset: int, align: int) -> bytes:
    """
    Baut [Header-Tabelle][Padding][Daten] und gibt den Block zurück.
    `start_offset` ist die Position der ersten Header-Zeile relativ zur Basis
    (0 = diese Liste beginnt bei der Basis).
    Alle Offset-Felder werden relativ zur Basis geschrieben.
    """
    n = len(entries)
    positions = []
    pos = start_offset
    for _ in entries:
        positions.append(pos)
        pos += ENTRY_SIZE
    data_start = _align(pos, align)

    data_cursor = data_start
    blocks: List[bytes] = []
    header_rows: List[bytes] = []

    for e, hdr_pos in zip(entries, positions):
        e.hdr_pos = hdr_pos
        if e.is_dir:
            # Kinder liegen ab dem Ende des Verzeichnis-Headers; ihre Offsets
            # sind relativ zur Position des Verzeichnis-Headers.
            sub_start = data_cursor - hdr_pos
            block = _build_list(e.children, start_offset=sub_start, align=align)
            e.offset_field = data_cursor
            e.size_field = UNDEFINED
            blocks.append(block)
            data_cursor += len(block)
        else:
            e.offset_field = data_cursor
            e.size_field = len(e.data)
            e.data_crc = crc16(e.data) if e.data else 0xFFFF
            blocks.append(e.data)
            data_cursor += len(e.data)
        header_rows.append(_entry_row(e))

    head = b"".join(header_rows)
    padding = b"\x00" * (data_start - (start_offset + n * ENTRY_SIZE))
    return head + padding + b"".join(blocks)


def _entry_row(e: Entry) -> bytes:
    body = struct.pack("<HII", e.data_crc, e.offset_field, e.size_field)
    body += bytes([e.attr, 0])
    body += struct.pack("<H", e.index)
    body += e.name.encode("utf-8")[:15].ljust(16, b"\x00")
    assert len(body) == 30, len(body)
    return struct.pack("<H", crc16(body)) + body


def build_image(entries: List[Entry], header: FlashHeader) -> bytes:
    body = _build_list(entries, start_offset=FLASH_HDR_SIZE, align=header.align_bytes)
    return header.build() + body


# --------------------------------------------------------------------- CLI

def _load(path: str) -> Image:
    with open(path, "rb") as fh:
        return Image(fh.read())


def _find(image: Image, name: str) -> Entry:
    for e, path in image.root().walk():
        if path == name or e.name == name:
            return e
    raise SystemExit(f"Datei nicht gefunden: {name}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="JieLi/JLFS Flash-Image-Werkzeug")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("info", help="Header-Infos anzeigen")
    s.add_argument("image")

    s = sub.add_parser("list", help="Dateibaum anzeigen")
    s.add_argument("image")

    s = sub.add_parser("extract", help="Dateien auspacken")
    s.add_argument("image")
    s.add_argument("-o", "--out", default="dump")

    s = sub.add_parser("verify", help="CRCs prüfen")
    s.add_argument("image")

    s = sub.add_parser("patch", help="Datei ersetzen (gleiche Größe empfohlen)")
    s.add_argument("image")
    s.add_argument("--name", required=True)
    s.add_argument("--file", required=True)
    s.add_argument("-o", "--out", required=True)

    s = sub.add_parser("json", help="Dateibaum als JSON")
    s.add_argument("image")

    a = p.parse_args(argv)

    try:
        img = _load(a.image)
    except (OSError, ValueError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1

    if a.cmd == "info":
        h = img.header
        print(f"Header-Offset : 0x{img._hdr_offset:04X}")
        pid = h.pid.rstrip(b"\x00").decode("utf-8", "replace")
        vid = repr(h.vid.rstrip(b"\x00"))
        print(f"PID           : {pid}")
        print(f"VID           : {vid}")
        print(f"Flash-Größe   : {h.flash_size} B ({h.flash_size / 1048576:.1f} MiB)")
        print(f"FS-Version    : {h.fs_ver}")
        print(f"Block-Align   : {h.align_bytes} B")
        print(f"Burner-Size   : {h.burner_size}")
        print(f"Einträge      : {len(img.entries)}")
    elif a.cmd == "list":
        print(img.list_text())
    elif a.cmd == "extract":
        img.extract(a.out)
        print(f"Nach {a.out}/ entpackt.")
    elif a.cmd == "verify":
        probs = img.verify()
        if probs:
            print("Probleme:")
            for x in probs:
                print("  " + x)
            return 2
        print("Alle Daten-CRCs OK.")
    elif a.cmd == "json":
        out = []
        for e, path in _walk_entries(img.entries):
            out.append({"path": path, "dir": e.is_dir, "size": len(e.data),
                        "attr": e.attr, "crc": e.data_crc})
        print(json.dumps(out, indent=2, ensure_ascii=False))
    elif a.cmd == "patch":
        with open(a.file, "rb") as fh:
            new = fh.read()
        e = _find(img, a.name)
        if len(new) != len(e.data) and len(e.data) > 0:
            print(f"Warnung: Größe ändert sich ({len(e.data)} -> {len(new)}). "
                  "Layout wird neu gebaut.", file=sys.stderr)
            e.data = new
            e.size_field = len(new)
            out = build_image(img.entries, img.header)
        else:
            out = bytearray(img.raw)
            start = e.data_pos
            out[start:start + len(new)] = new
            crc = crc16(new)
            struct.pack_into("<H", out, e.hdr_pos + 2, crc)
            struct.pack_into("<H", out, e.hdr_pos,
                             crc16(bytes(out[e.hdr_pos + 2:e.hdr_pos + 32])))
            out = bytes(out)
        with open(a.out, "wb") as fh:
            fh.write(out)
        print(f"Geschrieben: {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
