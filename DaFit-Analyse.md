# Da Fit – APK-Analyse: Lässt sich die Uhr über die App beeinflussen?

Stand: 19.10.2026 · Analysiert: `Da Fit.apk` aus `DaFit.zip` (Google Drive)

---

## 1. Was ist in der Datei?

| | |
|---|---|
| Archiv | `DaFit.zip` (83,2 MB, SHA-256 `633a9544…9124cd91`) |
| Enthält | **`Da Fit.apk`** – 157.504.139 Bytes (150 MB) |
| SHA-256 APK | `4aa538efa0bf2c46e0ba70b5ab4c2099f9a0a9235c09f660046f0ff285ae7a7b` |
| MD5 APK | `9e4fd8b24df9473e3187a0eaedf49739` |
| Paket | `com.crrepa.band.dafit` |
| App-Name | Da Fit |
| Version | `2.9.24-1-g4ae1f0a37a` (versionCode **22220**) |
| minSdk / targetSdk | 23 / 36 |
| Signatur | v1 + v2 + v3, Zertifikat `CN=moyoung, O=…, Shenzhen, CN` (Original-Hersteller) |
| Umfang | 13 DEX-Dateien, ~117.000 Klassen, ~710.000 Methoden, 54 Berechtigungen, 284 Activities |
| Hauptkomponente | `com.crrepa.band.my.SplashActivity` |

Die App ist eine Standard-„Da Fit“-Installation auf Basis des **CRREPA-BLE-SDK**
(`com.crrepa.ble.*`). Der gesamte Armband-Teil läuft über dieses SDK, der
App-Teil (`com.crrepa.band.my.*`) ist nur UI, Cloud-Sync und Health-Daten.

---

## 2. Kurzantwort

**Ja, es gibt mehrere Wege – und der einfachste braucht nicht mal Modding.**

Die App hat **keine eigene Uhrzeit-Einstellung** für das Band. Sie schickt
beim Verbinden (und bei jeder Zeit-/Zeitzonenänderung des Handys) einfach die
**Handy-Zeit** ans Band. Das heißt:

> Die Uhr am Armband ist ein 1:1-Spiegel der Handy-Uhr. Ändere die Handy-Zeit
> oder -Zeitzone, und das Band übernimmt sie beim nächsten Sync.

Wer eine **frei wählbare, von der Handy-Zeit abweichende Uhrzeit** will, muss
entweder die App patchen (Weg B) oder direkt per BLE sprechen (Weg C, siehe
Abschnitt 3 – das Paketformat ist vollständig rekonstruiert).

---

## 3. Was ich im Code gefunden habe

### 3.1 Der Aufrufpfad

```
f51.sendConnectMessage()                 → nach erfolgreichem Verbindungsaufbau
TimeChangeReceiver.onReceive()           → bei ACTION_TIME_CHANGED / TIMEZONE_CHANGED
        └─> ps0.sendCurrentTime()
                 └─> CRPBleConnection.syncTime()            (com.crrepa.i.a)
                 └─> CRPBleConnection.sendCurrentTimezone()
```

`ps0.sendCurrentTime()` ist eine RxJava-Action, die exakt zwei Dinge tut:
`syncTime()` und `sendCurrentTimezone()`.

### 3.2 Die SDK-Schnittstelle (`com.crrepa.ble.conn.CRPBleConnection`, 560 Methoden)

Relevant für „Uhr“:

| Methode | Bedeutung |
|---|---|
| `syncTime()` | Datum + Uhrzeit senden |
| `sendCurrentTimezone()` | UTC-Offset senden |
| `sendTimeSystem(byte)` | 12 h / 24 h |
| `sendDisplayTime(int)` | Anzeige-Zeitmodus |
| `sendWatchFace(...)`, `sendPhotoWatchFace(...)` | Ziffernblätter |
| `startFirmwareUpgrade(...)`, `abortFirmwareUpgrade()` | Firmware-OTA |

### 3.3 Der Paket-Builder (`com.android.mltcode.paycertificationapi.hdb`)

Alle Kommandos werden mit derselben Funktion gebaut: `hdb.a(int cmd, byte[] payload)`.

```
┌────────┬────────┬───────────────┬──────────┬─────────┬───────────────┐
│ 0xFE   │ 0xEA   │ 0x20/0x10 …   │ Länge    │ Cmd     │ Payload …     │
└────────┴────────┴───────────────┴──────────┴─────────┴───────────────┘
  Byte 0   Byte 1   Byte 2          Byte 3     Byte 4    ab Byte 5
```

* **Byte 2** = `0x10`, wenn die Protokollversion 20 ist, sonst `0x20`
  (bei Paketen > 255 Bytes: `0x20 + (len >> 8)`).
* **Byte 3** = Gesamtlänge = `Payload-Länge + 5` (Low-Byte).
* **Byte 4** = Kommando.
* Kein CRC, keine Verschlüsselung, keine Signatur auf dieser Ebene.

### 3.4 Zeit setzen (`mrc.b()` → `ckb.c()`)

```
CMD 0x31
Payload (5 Byte):
  [0..3]  Unix-Sekunden, Big Endian, „Wanduhrzeit im GMT+8-Rahmen“
  [4]     konstant 0x08
```

Die App rechnet: aktuelle lokale Zeit als String formatieren und **als GMT+8
interpretieren**. Praktisch heißt das für einen eigenen Sender:

```
payload_ts = unix(gewünschte_Wanduhrzeit interpretiert als UTC) − 8·3600
```

Beispiel: Band soll **2026-10-19 12:00:00** zeigen →
`1792382400` → `6A D5 95 C0`
→ komplettes Paket: `FE EA 20 0A 31 6A D5 95 C0 08`

### 3.5 Zeitzone (`mrc.c()` → `ckb.a()`)

```
CMD 0xBB (-69)
Payload (6 Byte): 07 00 + int32 BE (aktueller UTC-Offset in Sekunden, signed)
```

Beispiel UTC+2: `07 00 00 00 1C 20` · UTC−5: `07 00 FF FF B9 B0`

### 3.6 Weitere relevante Kommandos

| CMD | Payload | Funktion |
|---|---|---|
| `0x17` | 1 Byte | Zeitformat (12/24 h) |
| `0x7D` | 1 Byte | Display-Zeitmodus |
| `0x27` | – | Abfrage |
| `0x31` | 5 Byte | **Zeit setzen** |
| `0xBB` | 6 Byte | **Zeitzone setzen** |

### 3.7 Der BLE-Transport

Die Service-/Charakteristik-Erkennung in `w1b.<init>` filtert auf Services,
deren UUID `"feea"` enthält, und merkt sich die Charakteristiken:

`FEE1, FEE2, FEE3, FEE5, FEE6, FEE7, FEE8, FEE9, FEEA`
→ also `0000feeX-0000-1000-8000-00805f9b34fb`, dazu Standard-Services
`180A` (Device Info), `180F` (Battery), `180D` (Heart Rate) und `3802`.
Ein zweiter Satz `6E40FF02/6E40FF03-…` (Nordic-UART-Stil) ist für andere
Chip-Familien vorgesehen.

Schreib-Routing im SDK (`ckb.f()`):

| Kanal | Charakteristik |
|---|---|
| 1 | FEE5 |
| 2 | FEE6 |
| 3 | 4A02 (Service 3802, Datei-/TP-Transfer) |
| 4 | FEEA (Fallback FEE9) |
| sonst (u. a. 0 und 8) | **FEE2** |

`syncTime()` benutzt Kanal 8 (mit Response), `sendCurrentTimezone()` Kanal 0 –
beide landen also bei FEE2. Beim Senden wird das Paket in Häppchen von
`MTU − 3` Bytes zerlegt.

> ⚠️ Das Routing ist aus dem Bytecode rekonstruiert. Ob dein konkretes Modell
> auf FEE2 oder FEE1 hört, verrät ein Blick mit **nRF Connect** oder ein
> BLE-Sniffer (Android: „Bluetooth HCI Snoop Log“ in den Entwickleroptionen).

---

## 4. Die drei praktischen Wege

### Weg A – ohne Modding (5 Minuten)
1. Handy-Zeit oder Zeitzone verstellen.
2. App öffnen und verbinden lassen (oder kurz Flugmodus an/aus, damit der
   `TimeChangeReceiver` feuert).
3. Band zeigt die neue Zeit.

Grenzen: nur „Handy-Zeit“, also keine freie Wahl unabhängig vom Handy;
Alarme/Timer auf dem Band verschieben sich mit; das Handy selbst leidet mit
(WhatsApp, Kalender, 2FA …).

### Weg B – APK patchen (smali)
In `com.android.mltcode.paycertificationapi.mrc.b()` vor dem Senden einen
Offset auf die Millisekunden addieren (z. B. `+3h`), APK neu bauen und
signieren. Danach:

* Signatur ändert sich → die Original-App muss deinstalliert werden
  (Health-Daten vorher exportieren/sichern!).
* Cloud-Login, Google-Fit-Anbindung und Play Protect können zicken.
* Beim nächsten App-Update ist der Patch weg (neu patchen).
* **Nicht weitergeben** – veränderte Fremd-APK zu verteilen ist rechtlich heikel.

Sinnvoll nur, wenn du dauerhaft eine verschobene Uhr willst und kein zweites
Gerät zum Steuern hast.

### Weg C – eigener BLE-Client (empfohlen, kein Eingriff in die App)
Mit einem kleinen Skript (siehe `tools/dafit_set_time.py`) verbindest du dich
direkt mit dem Band und schickst die Pakete aus Abschnitt 3.4/3.5. Damit kannst
du **jede beliebige Uhrzeit** setzen – auch eine, die es gar nicht gibt.

Voraussetzungen: Bluetooth am PC/Laptop (Linux, Windows, macOS), Python +
`bleak`. Auf Android geht das nur mit Root/Termux, weil BLE-Zugriff dort
normalerweise an die System-API gebunden ist.

Wichtig: Das Band überschreibt die Zeit beim nächsten App-Verbinden wieder.
Also entweder die App danach nicht mehr verbinden lassen, oder den Weg
dauerhaft per eigenem Tool nutzen.

---

## 5. Ziffernblätter & Firmware (falls „Uhr modifizieren“ das gemeint war)

**Ziffernblätter:** Offiziell vorgesehen und in der App enthalten:

* `DressPhotoCropActivity` → `sendPhotoWatchFace(List, Bitmap, listener)`:
  eigenes Foto als Ziffernblatt.
* `WatchFaceEditActivity`, `WatchFaceStoreMainActivity`,
  `PremiumWatchFaceActivity`: Store, Bearbeiten, Premium-Blätter.
* Das SDK kennt getrennte Watchface-Typen je Chipfamilie: **Jieli, Hisilicon,
  Sifli, Bluetrum** (`CRPJieliWatchFaceInfo`, `CRPHisiliconWatchFaceInfo`,
  `CRPSifliSupportWatchFaceInfo`, `CRPBluetrumWatchFaceInfo`).

Beliebige eigene Layouts (Zeigerposition, Schriftarten) sind nur über den
Store bzw. die Foto-Funktion vorgesehen. Fremde Watchface-Dateien direkt zu
schieben geht theoretisch über dieselben Transfer-Kommandos, ist aber pro
Chipfamilie ein eigenes Dateiformat und ohne Testgerät nicht sinnvoll
reverse-engineerbar.

**Firmware:** `startFirmwareUpgrade(...)`, `DfuService` und chipabhängige
Controller (`com.crrepa.ble.ota.goodix`, `.hisilicon`, `.jieli`, `.realtek`,
`.sifli`) sind vorhanden. Firmware-Images sind herstellersigniert – ein
eigenes Firmware-Mod ist damit kein realistischer Weg.

---

## 6. Risiken & Einschränkungen

* **Nicht an einem echten Band getestet.** Alle Aussagen stammen aus der
  statischen Analyse des Bytecodes; das Protokoll ist aber sehr klar lesbar
  (keine Obfuskation der Kommando-Builder, keine Verschlüsselung).
* Falsche Zeit ↔ **Alarme, Erinnerungen, Schlaf-/Aktivitäts-Auswertung**
  laufen schief; die Cloud-Auswertung kann Daten verwerfen.
* Bei Weg B: Original-App deinstallieren = lokale Daten weg, sofern kein
  Cloud-Backup.
* Beim Spielen mit Firmware-Kommandos besteht **Brick-Gefahr**. Für Zeit/
  Ziffernblatt unkritisch, für OTA nicht.
* Rechtlich unkritisch, solange du nur dein eigenes Gerät und deine eigene
  App-Installation veränderst.

---

## 7. Anhang: Beispielpakete

| Zweck | Bytes |
|---|---|
| Zeit 2026-10-19 12:00:00 | `FE EA 20 0A 31 6A D5 95 C0 08` |
| Zeit 2026-01-01 00:00:00 | `FE EA 20 0A 31 69 55 48 80 08` |
| Zeitzone UTC+2 | `FE EA 20 0B BB 07 00 00 00 1C 20` |
| Zeitzone UTC−5 | `FE EA 20 0B BB 07 00 FF FF B9 B0` |
| 24-h-Modus | `FE EA 20 06 17 01` |

(Alle Werte aus den Bytecode-Konstanten bzw. der Rechenlogik in
`mrc.b()` / `mrc.c()` abgeleitet.)

---

## 8. Dateien in diesem Repo

| Datei | Inhalt |
|---|---|
| `DaFit-Analyse.md` | dieser Bericht |
| `tools/dafit_set_time.py` | Beispiel-Client (bleak), setzt Zeit/Zeitzone/Format per BLE |

Die APK selbst ist **nicht** eingecheckt (150 MB, und sie gehört dem Hersteller).
Der ursprüngliche Google-Drive-Link bleibt die Quelle.
