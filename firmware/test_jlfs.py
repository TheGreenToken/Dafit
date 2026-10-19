#!/usr/bin/env python3
"""
Tests für jlfs.py.

Der wichtigste Test ist der CRC-Test: die drei Einträge aus der
öffentlich dokumentierten tone.idx-Struktur müssen mit unserem
CRC-16/XMODEM exakt die dort angegebenen Prüfsummen ergeben.
Damit ist der CRC-Algorithmus belegt und nicht geraten.
"""

import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jlfs
from jlfs import Entry, FlashHeader, Image, build_image, crc16


# ------------------------------------------------ CRC gegen die Doku

def test_crc_matches_documented_tone_idx():
    # Quelle: kagaimiq.github.io/jielie/datafmt/newfw.html, Abschnitt tone.idx
    assert crc16(bytes.fromhex("ffffffffffff08000000")) == 0x5BD4
    assert crc16(bytes.fromhex("0701627400")) == 0x20F6
    assert crc16(bytes.fromhex("0c0262745f636f6e6e00")) == 0x014A


def test_crc_known_vector():
    # CRC-16/XMODEM Standardvektor
    assert crc16(b"123456789") == 0x31C3


# ------------------------------------------------ Round-Trip

def _sample_image() -> bytes:
    app = Entry(name="app.bin", attr=jlfs.ATTR_TYPE_FILE, data=b"APP" * 40)
    cfg = Entry(name="cfg_tool.bin", attr=jlfs.ATTR_TYPE_FILE, data=b"CFG" * 12)

    app_dir = Entry(name="app_dir_head", attr=jlfs.ATTR_TYPE_DIR, is_dir=True,
                    children=[app, cfg])
    uboot = Entry(name="uboot.boot", attr=jlfs.ATTR_TYPE_UBOOT, data=b"UB" * 64)
    cfgini = Entry(name="isd_config.ini", attr=jlfs.ATTR_TYPE_FILE,
                   data=b"SYS_CFG\0\x01\x02\x03")
    key = Entry(name="key_mac", attr=jlfs.ATTR_TYPE_FILE | jlfs.ATTR_SPECIAL,
                data=bytes(range(16)))

    header = FlashHeader(burner_size=0x1234, vid=b"V1.0", flash_size=8 * 1024 * 1024,
                         fs_ver=3, block_align=1, pid=b"VYG2")
    return build_image([uboot, cfgini, app_dir, key], header)


def test_roundtrip_top_level():
    img = Image(_sample_image())
    names = [e.name for e in img.entries]
    assert names == ["uboot.boot", "isd_config.ini", "app_dir_head", "key_mac"]


def test_roundtrip_nested_dir():
    img = Image(_sample_image())
    app_dir = img.entries[2]
    assert app_dir.is_dir
    assert [c.name for c in app_dir.children] == ["app.bin", "cfg_tool.bin"]
    assert app_dir.children[0].data == b"APP" * 40


def test_verify_clean_image():
    img = Image(_sample_image())
    assert img.verify() == []


def test_extract(tmp_path):
    img = Image(_sample_image())
    img.extract(str(tmp_path))
    assert (tmp_path / "app_dir_head" / "app.bin").read_bytes() == b"APP" * 40


def test_patch_same_size_fixes_crc(tmp_path):
    raw = _sample_image()
    img = Image(raw)
    target = img.entries[2].children[0]
    start = target.data_pos
    new = b"XYZ" * 40

    patched = bytearray(raw)
    patched[start:start + len(new)] = new
    struct.pack_into("<H", patched, target.hdr_pos + 2, crc16(new))
    struct.pack_into("<H", patched, target.hdr_pos,
                     crc16(bytes(patched[target.hdr_pos + 2:target.hdr_pos + 32])))

    img2 = Image(bytes(patched))
    assert img2.entries[2].children[0].data == new
    assert img2.verify() == []


def test_flash_header_roundtrip():
    h = FlashHeader(burner_size=7, vid=b"AB", flash_size=0x400000,
                    fs_ver=2, block_align=4, pid=b"TEST")
    h2 = FlashHeader.parse(h.build())
    assert (h2.burner_size, h2.vid.rstrip(b"\x00"), h2.flash_size,
            h2.fs_ver, h2.block_align) == (7, b"AB", 0x400000, 2, 4)


def test_header_offset_0x1000():
    raw = b"\x00" * 0x1000 + _sample_image()
    img = Image(raw)
    assert img._hdr_offset == 0x1000
    assert img.entries[0].name == "uboot.boot"
