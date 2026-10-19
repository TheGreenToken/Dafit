#!/usr/bin/env python3
"""
Da Fit / CRREPA-Band: Uhrzeit per BLE setzen.

Beispiel:
    python3 dafit_set_time.py --scan
    python3 dafit_set_time.py --device "AA:BB:CC:DD:EE:FF" --time "2026-10-19 12:00"
    python3 dafit_set_time.py --device "AA:BB:CC:DD:EE:FF" --offset +3h
    python3 dafit_set_time.py --time "2026-10-19 12:00" --dry-run

Protokoll (aus der APK rekonstruiert):
    Frame:  FE EA 20 <len> <cmd> <payload...>      len = len(payload) + 5
    0x31   Zeit:     4 Byte Unix-Sekunden (BE) der Wanduhrzeit "als GMT+8" + 0x08
    0xBB   Zeitzone: 07 00 + int32 BE (UTC-Offset in Sekunden, signed)
    0x17   Zeitformat (12/24h): 1 Byte

Abhängigkeit:  pip install bleak
"""

from __future__ import annotations

import argparse
import asyncio
import struct
import sys
from datetime import datetime, timedelta, timezone

SERVICE_UUID = "0000feea-0000-1000-8000-00805f9b34fb"
# Reihenfolge = Ausprobieren. Laut SDK-Routing schreibt die App auf FEE2.
CANDIDATE_CHARS = [
    "0000fee2-0000-1000-8000-00805f9b34fb",
    "0000fee1-0000-1000-8000-00805f9b34fb",
    "0000fee3-0000-1000-8000-00805f9b34fb",
]

GMT8 = 8 * 3600


# ----------------------------------------------------------------- Protokoll

def build_frame(cmd: int, payload: bytes = b"") -> bytes:
    """Baut ein CRREPA-Kommandopaket."""
    total = len(payload) + 5
    if total > 0xFF:
        b2 = ((total >> 8) + 0x20) & 0xFF
    else:
        b2 = 0x20
    return bytes([0xFE, 0xEA, b2, total & 0xFF, cmd & 0xFF]) + payload


def time_packet(wall_clock: datetime) -> bytes:
    """
    wall_clock = gewünschte Anzeigezeit auf dem Band (naiv, ohne tzinfo).
    Die App formatiert die lokale Zeit und interpretiert sie als GMT+8,
    daraus folgt:  ts = unix(wall_clock als UTC) - 8h.
    """
    naive_utc = wall_clock.replace(tzinfo=timezone.utc)
    ts = int(naive_utc.timestamp()) - GMT8
    payload = struct.pack(">I", ts & 0xFFFFFFFF) + b"\x08"
    return build_frame(0x31, payload)


def timezone_packet(offset_seconds: int) -> bytes:
    payload = bytes([0x07, 0x00]) + int(offset_seconds).to_bytes(4, "big", signed=True)
    return build_frame(0xBB, payload)


def time_format_packet(is_24h: bool) -> bytes:
    return build_frame(0x17, bytes([1 if is_24h else 0]))


# --------------------------------------------------------------------- BLE

async def scan(timeout: float = 8.0) -> None:
    from bleak import BleakScanner

    print(f"Suche {timeout:.0f}s nach BLE-Geräten …\n")
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    rows = []
    for dev, adv in devices.values():
        name = dev.name or adv.local_name or "?"
        rows.append((name, dev.address, adv.rssi))
    for name, addr, rssi in sorted(rows, key=lambda r: -r[2]):
        print(f"  {rssi:4d} dBm  {addr}  {name}")

    print("\nHinweis: 'Da Fit'-Bänder heißen oft 'Da Fit-XXXX' oder 'DF-XXXX'.")


async def write_chunks(client, char, data: bytes) -> None:
    """Schreibt ein Paket in MTU-3-Häppchen (wie die App)."""
    try:
        mtu = client.mtu_size or 23
    except Exception:
        mtu = 23
    chunk = max(20, mtu - 3)
    for i in range(0, len(data), chunk):
        part = data[i:i + chunk]
        await client.write_gatt_char(char, part, response=True)
        await asyncio.sleep(0.03)


async def find_write_char(client):
    services = client.services
    candidates = []
    for service in services:
        if "feea" in str(service.uuid).lower():
            candidates.extend(service.characteristics)
    if not candidates:
        return None
    by_uuid = {str(c.uuid).lower(): c for c in candidates}
    for uuid in CANDIDATE_CHARS:
        if uuid in by_uuid:
            return by_uuid[uuid]
    # irgendeine schreibbare Charakteristik im FEEA-Service
    for c in candidates:
        if "write" in c.properties:
            return c
    return None


async def run(args) -> int:
    if args.scan:
        await scan()
        return 0

    if args.offset:
        target = datetime.now() + args.offset
    elif args.time:
        target = datetime.strptime(args.time, "%Y-%m-%d %H:%M")
    else:
        target = datetime.now()

    tz_hours = args.tz if args.tz is not None else (datetime.now().astimezone().utcoffset().total_seconds() / 3600)
    tz_seconds = int(round(tz_hours * 3600))

    frames = [
        ("Zeit     0x31", time_packet(target)),
        ("Zeitzone 0xBB", timezone_packet(tz_seconds)),
    ]
    if args.time_format:
        frames.append(("Format   0x17", time_format_packet(args.time_format == "24")))

    print(f"Zielzeit auf dem Band : {target:%Y-%m-%d %H:%M:%S} (Wanduhr)")
    print(f"Zeitzone              : UTC{tz_seconds/3600:+.1f}")
    print()
    for label, frame in frames:
        print(f"  {label}: {frame.hex(' ').upper()}")

    if args.dry_run:
        print("\n--dry-run: nichts gesendet.")
        return 0

    if not args.device:
        print("\nFehler: --device <MAC> angeben (oder --scan).", file=sys.stderr)
        return 2

    from bleak import BleakClient

    print(f"\nVerbinde mit {args.device} …")
    async with BleakClient(args.device, timeout=20.0) as client:
        if not client.is_connected:
            print("Verbindung fehlgeschlagen.", file=sys.stderr)
            return 1

        services = client.services
        if not any("feea" in str(s.uuid).lower() for s in services):
            print(
                "\nWarnung: kein FEEA-Service gefunden. Gefundene Services:\n  "
                + "\n  ".join(str(s.uuid) for s in services),
                file=sys.stderr,
            )

        char = await find_write_char(client)
        if char is None:
            print("Keine passende Schreib-Charakteristik gefunden.", file=sys.stderr)
            return 1

        print(f"Schreibe auf {char.uuid}")
        for label, frame in frames:
            await write_chunks(client, char, frame)
            print(f"  → {label} gesendet ({len(frame)} Bytes)")
            await asyncio.sleep(0.15)

    print("\nFertig. Das Band sollte die neue Zeit jetzt anzeigen.")
    print("Achtung: Beim nächsten Verbinden mit der Da-Fit-App wird die Zeit")
    print("        wieder auf die Handy-Zeit zurückgesetzt.")
    return 0


def parse_offset(text: str) -> timedelta:
    """'+3h', '-90m', '+45s' → timedelta"""
    text = text.strip()
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("+-")
    unit = text[-1].lower()
    value = float(text[:-1])
    if unit == "h":
        return timedelta(hours=sign * value)
    if unit == "m":
        return timedelta(minutes=sign * value)
    if unit == "s":
        return timedelta(seconds=sign * value)
    raise argparse.ArgumentTypeError("Offset wie +3h, -90m oder +45s angeben")


def main() -> int:
    p = argparse.ArgumentParser(description="Da Fit / CRREPA: Uhrzeit per BLE setzen")
    p.add_argument("--scan", action="store_true", help="nur Geräte suchen")
    p.add_argument("--device", help="MAC-Adresse des Bands (z. B. AA:BB:CC:DD:EE:FF)")
    p.add_argument("--time", help='gewünschte Zeit "YYYY-MM-DD HH:MM"')
    p.add_argument("--offset", type=parse_offset, help="Offset zur aktuellen Zeit, z. B. +3h")
    p.add_argument("--tz", type=float, help="UTC-Offset in Stunden (Default: lokale Zeitzone)")
    p.add_argument("--time-format", choices=["12", "24"], help="Zeitformat setzen")
    p.add_argument("--dry-run", action="store_true", help="nur Pakete anzeigen")
    args = p.parse_args()

    if args.tz is not None and not (-14 <= args.tz <= 14):
        p.error("--tz muss zwischen -14 und +14 liegen")

    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
