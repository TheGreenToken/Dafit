# Komplett ersetzen: eigene Firmware für die ZL99J (JL7012F6)

Ziel: die Stock-Firmware `MOY-VYG2-2.0.8` vollständig durch eigene Software
ersetzen. Dieses Dokument ist der Bauplan — inklusive der Stellen, an denen es
weh tut.

---

## 1. Was „komplett ersetzen" technisch heißt

Die Boot-Kette einer JieLi-Uhr:

```
Mask-ROM (im Chip, unveränderbar)
   └── uboot.boot        zweite Stufe, initialisiert Hardware nach isd_config.ini
          └── app.bin    die eigentliche Uhr-Firmware  ← hier greifen wir an
```

* **ROM-Bootloader**: nicht ersetzbar. Er entscheidet auch, ob ein Image
  überhaupt angenommen wird (Upgrade-Modus, ggf. Signaturprüfung).
* **uboot.boot**: theoretisch ersetzbar (Quellcode: `Jieli-Tech/fw-Bootloader`,
  AC701N → `br28`). Praktisch nur sinnvoll, wenn du die Board-Konfiguration
  exakt kennst. **Empfehlung: behalten.**
* **app.bin**: das ist deine Firmware. Vollständig ersetzbar.

„Komplett ersetzen" heißt also konkret: **eigenes `app.bin` bauen, in ein
JLFS-Image packen, über die vorhandene OTA-Kette flashen.** Die unteren Stufen
bleiben, weil sie Hardware-Wissen enthalten, das man sonst mühsam
rekonstruieren müsste.

---

## 2. Was an Werkzeugen und Quellen existiert

| Was | Wo | Zustand |
|---|---|---|
| Bootloader-Quellen (AC701N/br28) | `github.com/Jieli-Tech/fw-Bootloader` | öffentlich |
| RCSP-OTA-SDK (Android/iOS/Flutter) | `github.com/Jieli-Tech/Android-JL_OTA` u. a. | öffentlich |
| Flash-/Dump-Tool für AC701N | `github.com/kagaimiq/jl-uboot-tool` | funktioniert laut Autor |
| Firmware-Format (JLFS, BANKCB, LZ4) | `kagaimiq.github.io/jielie/datafmt/` | dokumentiert |
| Chip-Doku, SDK-Handbücher | `doc.zh-jieli.com` | öffentlich, teils chinesisch |
| AC701N-Watch-SDK | über JieLi-Distributoren (z. B. YunThinker) | Registrierung nötig |
| Toolchain + `isd_download.exe` | JieLi-Paketmanager | Registrierung nötig |
| Dieses Repo | `firmware/jlfs.py` | Parser/Patcher, getestet |

---

## 3. Was wir noch nicht wissen (und wie wir es rausfinden)

Alles davon steht im Stock-Image — deshalb ist Schritt 1 der Dump.

| Unbekannt | Fundort |
|---|---|
| LCD-Controller + Init-Sequenz | `app.bin`/`uboot.boot`, Suchmuster für SPI-Init |
| Panel-Timing (240×296) | `isd_config.ini` + uboot |
| Touch CST820 (I2C-Adresse, IRQ-Pin) | `app.bin` |
| PPG HX3600 / LC09A | `app.bin` |
| Accel SC7A20 / DA267 | `app.bin` |
| Pinmux, GPIO-Belegung | `uboot.boot` + `isd_config.ini` |
| Flash-Größe (64 Mb = 8 MiB) | Flash-Header (`flash_size`) |
| Dual- oder Single-Bank-OTA | Vorhandensein zweier `app`-Bereiche im Image |
| Ob das ROM signierte Images prüft | Test: unverändertes Image neu flashen |

---

## 4. Phasenplan

### P0 — Sicherheitsnetz (ohne das nicht anfangen)

1. Uhr öffnen, UART-Pads suchen (TX/RX/GND, meist 3 Pads nahe Akku/SoC).
2. USB-TTL-Adapter (3,3 V!) anschließen, Log mitlesen.
3. Kompletten Flash mit `jl-uboot-tool` dumpen: `dump.bin`, 8 MiB.
4. Gegenprobe: denselben Dump zurückschreiben, Uhr bootet normal.
5. **Erst danach** an Modifikationen denken.

Wenn kein UART-Pad erreichbar ist: Forced-Download über Flash-MISO → GND beim
Boot. Vorher Datenblatt-Pinout des JL7012F6 (QFN52) besorgen.

### P1 — Stock-Image verstehen

```bash
python3 jlfs.py info  dump.bin
python3 jlfs.py list  dump.bin
python3 jlfs.py extract dump.bin -o stock/
python3 jlfs.py verify dump.bin
```

Erwartete Struktur: `uboot.boot`, `isd_config.ini`, `app_dir_head`
(`app.bin`, `cfg_tool.bin`), `key_mac`, dazu Ressourcen-Verzeichnisse.

Ist der Dump gescrambelt (Header-CRC passt nicht), muss er zuerst
descrambelt werden; das Scrambling ist bei Quarkslab beschrieben, ein
Descrambler fehlt hier noch (siehe „Offene Punkte").

### P2 — 1:1-Rebuild flashen (der wichtigste Test)

`jlfs.py` neu bauen lassen, **ohne inhaltliche Änderung**, flashen.
Bootet die Uhr? Dann stimmen Layout, Ausrichtung und CRCs. Bootet sie nicht,
ist der Parser noch nicht byte-exakt — dann weiter analysieren, nicht raten.

### P3 — Erster echter Patch

Kleinster sinnvoller Eingriff: einen String in `app.bin` ersetzen
(gleiche Länge, CRC neu). Bootet und zeigt der String? Pipeline steht.

### P4 — Eigenes `app.bin`, erste Anzeige

Minimal-Firmware aus dem AC701N-SDK: Display-Init, ein Bild, fertig.
Das ist der Moment, in dem „eigene Firmware" real wird — und der schwierigste
Schritt, weil die LCD-Init-Sequenz aus dem Stock-Image rekonstruiert werden muss.

### P5 — BLE + Zeit

Der Moyoung-v2-Protokollteil ist dokumentiert (Gadgetbridge). Ein eigenes
`app.bin` kann dieselben Kommandos sprechen: `0x31` Zeit, `0xBB` Zeitzone,
`0x17` 12/24 h. Damit ist die Uhr wieder eine Uhr — nur mit deiner Software.

### P6 — Sensoren

PPG, Accel, Touch über I2C initialisieren. Erst roh auslesen und über BLE
streamen, dann auswerten. Ehrlicher Nebeneffekt: du siehst, welche „Messwerte"
der Stock-Firmware echt und welche generiert waren.

### P7 — UI

Eigenes Rendering auf 240×296. Wenn du bei P5/P6 angekommen bist, ist das
„nur noch" Arbeit.

---

## 5. Realistische Erwartung

| Ziel | Aufwand | Wahrscheinlichkeit |
|---|---|---|
| Stock-Image entpacken, Strings patchen | Tage | sehr hoch |
| 1:1-Rebuild flashen | Tage–Wochen | hoch |
| Eigenes `app.bin` mit Display + Zeit + BLE | 1–3 Monate | mittel |
| Feature-Parität mit Stock | 1–2 Jahre | niedrig |
| „InfiniTime für JieLi" | Community-Projekt | nur gemeinsam |

Der ehrliche Satz: **Die ersten drei Zeilen sind ein Projekt, die vierte ist
ein Hobby, die fünfte ist ein Lebenswerk.** Die gute Nachricht: nach P5 hast du
eine Uhr, die dir gehört.

---

## 6. Einkaufsliste

* USB-TTL-Adapter 3,3 V (CP2102/CH340) — für UART-Log und Dump
* Feine Lötspitze, Flussmittel, Mikroskop oder Lupe
* Öffnungswerkzeug (Gehäuse ist verklebt, IP68)
* Optional: JL-Forced-Download-Dongle (AC701N-Serie)
* Optional, aber empfohlen: **zweite ZL99J als Teilespender** (~20–30 €)

---

## 7. Werkzeug in diesem Ordner

```bash
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest firmware/test_jlfs.py -q

.venv/bin/python firmware/jlfs.py info      dump.bin
.venv/bin/python firmware/jlfs.py list      dump.bin
.venv/bin/python firmware/jlfs.py extract   dump.bin -o stock/
.venv/bin/python firmware/jlfs.py verify    dump.bin
.venv/bin/python firmware/jlfs.py patch     dump.bin --name app.bin --file my_app.bin -o out.bin
```

Der CRC ist **empirisch belegt**: `crc16` (CRC-16/XMODEM) reproduziert exakt
die drei in der öffentlichen `tone.idx`-Dokumentation angegebenen Prüfsummen.
Das ist kein Raten, das ist ein Test (`test_crc_matches_documented_tone_idx`).

Der Parser ist gegen synthetische Images getestet (Round-Trip, verschachtelte
Verzeichnisse, Header auf 0x1000, Patch mit CRC-Neuberechnung). **Gegen ein
echtes Stock-Image ist er noch nicht gelaufen** — das ist P1.

---

## 8. Offene Punkte im Werkzeug

* **Descrambler** für rohe Flash-Dumps fehlt (Scrambling-Algorithmus siehe
  Quarkslab-Artikel; muss noch implementiert und gegen einen Dump verifiziert
  werden).
* **Byte-exakter Rebuild** ist plausibel, aber nicht bewiesen; P2 klärt es.
* **LZ4-komprimiertes `uboot.boot`** (Flag 0x40) wird noch nicht entpackt.
* **`isd_config.ini`-Parser** (Schlüssel/Wert-Einträge) fehlt noch.

Sobald ein echter Dump vorliegt, ist die Reihenfolge: Parser gegen den Dump
laufen lassen → Abweichungen fixen → P2.
