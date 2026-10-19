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

---

# 9. Nachtrag: Kann man die komplette Software auf der Uhr modden?

Kurz: **Nein, nicht „komplett" – aber die App enthält mehr Angriffsfläche, als man
erwartet.** Entscheidend ist die Ebene: Ziffernblatt, UI-Ressourcen, Watch-Apps
und Firmware sind vier völlig verschiedene Baustellen.

## 9.1 Was der OTA-Pfad der App verrät

Der komplette Firmware-Weg ist im Bytecode nachvollziehbar
(`com.crrepa.ble.trans.upgrade.presenter.FirmwareUpgradePresenter`):

```
1. checkFirmwareVersion(version, type, callback)
     → api.moyoung.com/v2/upgrade/factory   (type != 1)
     → api.moyoung.com/v2/upgrade/beta      (type == 1)
     Parameter: version, mac, app_version=1.0.2
2. Antwort → FirmwareVersionInfo
     Felder: code, url, md5, size, version, type, mcu,
             log, log_en, path, sf_ui_ver,
             tp_bin, tp_bin_md5, tp_bin_path, tp_bin_offset, tp_bin_size
     → KEIN Signatur-, Zertifikats- oder Public-Key-Feld
3. downloadNewFirmware()
     → Cache-Treffer per MD5 → sonst Download der url
     → Prüfung ausschließlich: HexUtils.checkFileOfMd5(md5, file)
4. startFirmwareUpgrade(...) → zza → chip-spezifischer DFU-Controller
```

**Wichtig:** Die App prüft **keine Signatur** – nur die MD5 aus derselben
Server-Antwort. Das ist eine Integritäts-, keine Authentizitätsprüfung.
Ob der **Bootloader der Uhr** ein signiertes Image verlangt, steht *nicht* in
der APK; das Firmware-Image selbst liegt auch nicht in der APK (nur die URL).

### Der lokale Flash-Pfad, den die UI nie benutzt

`CRPBleConnection` hat drei `startFirmwareUpgrade`-Überladungen, darunter:

```java
startFirmwareUpgrade(boolean isGoodix, Uri uri, String fileName,
                     CRPBleFirmwareUpgradeListener listener)
```

Die Implementierung (`zza.a(Z, Uri, String, Listener)`) kopiert **jede beliebige
Content-Uri** nach `cache/firmware_upgrade/<timestamp>_<name>` und flasht sie –
**ohne MD5-Prüfung**. Die Da-Fit-Oberfläche ruft nur die Server-Variante auf
(`ps0$w1.call → startFirmwareUpgrade(Z, listener)`). Der lokale Pfad ist also
vorhanden, aber nicht über die UI erreichbar: ein gepatchtes Da Fit oder ein
eigener Client mit den SDK-Klassen könnte ihn nutzen.

### Übertragung: CRC ja, Signatur nein

Der Dateitransfer (`lcb` + `ahb`) chunked die Datei, das Gerät meldet am Ende
eine CRC, die App vergleicht sie (`checkFileCRC`) und antwortet mit
`sendFileCheckResult(ok)`. Das ist eine Transportkontrolle, keine
Authentifizierung.

## 9.2 Die vier Ebenen im Vergleich

| Ebene | Mechanismus in der App | Aufwand | Erfolgsaussicht |
|---|---|---|---|
| **Ziffernblatt** | `CRPWatchFaceInfo(File, type)` + `sendWatchFace(...)`; Foto-Ziffernblatt (`sendPhotoWatchFace`); Store `api.moyoung.com/faces/...` | niedrig | **hoch** (Foto offiziell, eigene .bin per Sideload plausibel) |
| **UI-Ressourcen** (Icons, Sprache, Layout) | `CRPUiTransInitiator.start(File, int, String)` (Cmd `0x6C`), `CRPBkTransInitiator` (Cmd `0xF7`, Beken), `CRPTpTransInitiator` (Touchpanel-FW) | mittel | **mittel** – Format proprietär, App nutzt es selbst nicht |
| **Watch-Apps** | `installApp(File, CRPAppTransListener)`, AppMarket (`api.moyoung.com`), OpenHarmony-Bridge (`device.appmarket.openharmony.*`) | hoch | **niedrig** – Pakete sind herstellerseitig signiert/geprüft |
| **Firmware (MCU)** | siehe 9.1, pro Chip eigene Controller | sehr hoch | **niedrig bis mittel, modellabhängig** |
| **Bootloader / Secure Boot** | nicht adressierbar | extrem hoch | **praktisch 0** ohne SWD und offenes Debug |

Chip-Familien, die das SDK unterstützt (`CRPMcuPlatform`):
`NORDIC, HUNTERSUN, REALTEK, GOODIX, SIFLI, JIELI, BLUETRUM, HISILICON`
(jeweils mit eigenem DFU-Controller: `com.crrepa.ble.ota.*`, `nrf.dfu.*`,
`com.realsil.sdk.*`, `sifli.dfu.*`). Der Nordic-Pfad ist die volle
nRF-DFU-Bibliothek inkl. „Bootloader from SDK 9.0 or newer. Signature
supported" – bei Nordic-Modellen ist eine Signaturprüfung also *möglich*,
aber optional und vom Hersteller konfiguriert.

## 9.3 Warum „komplette Software" trotzdem scheitert

1. **Kein Quellcode, kein Uhren-SDK.** Die App ist Closed Source; die
   Band-Firmware ist ein binäres Image ohne Doku.
2. **Firmware liegt nicht bei.** In der APK stehen nur die Download-URLs.
3. **Bootloader ist das Nadelöhr.** Die App prüft nichts, die Uhr schon –
   oder eben nicht. Das ist modellabhängig und ohne Testgerät nicht
   vorhersagbar.
4. **Brick-Risiko ohne Rettungsweg.** Recovery gibt es nur bei Goodix
   (`RestoreFirmwareActivity` / `RestoreFirmwareController`) und über die
   Stock-OTA. Ein halb geschriebenes Image macht das Band sonst zum
   Briefbeschwerer.
5. **Rechtlich.** Eigene Uhr flashen ist okay; veränderte Firmware
   weitergeben oder fremde Geräte flashen nicht.

## 9.4 Realistischer Fahrplan

1. **Modell und Chip bestimmen.** In der App unter Gerät → Firmware-Version
   steht ein String der Form `X-Y-Z`; `FirmwareVersionUtils.getFirmwareType()`
   nimmt Teil 2 als Typ. Alternativ mit nRF Connect die Services anschauen:
   `FEEA…` = CRREPA-Standard, `6E40FF02/6E40FF03` = Nordic-UART,
   `6473-5f696c666973` = SiFli.
2. **Eigenes OTA-Paket abfangen.** Proxy (mitmproxy) mit eigenem CA-Zertifikat
   plus gepatchter `network_security_config`, oder URL aus dem Logcat fischen.
   Das Zip ist ein normaler HTTP-Download; danach lässt sich das Format
   (Header, Magic, Version, evtl. Signaturblock) analysieren.
3. **Zuerst Ziffernblatt/UI testen** – dort ist ein Fehlschlag folgenlos.
4. **Erst dann Firmware.** Vorher: identisches Stock-Image besorgen, als
   Recovery in der Hinterhand.

Wer ein Firmware-Zip oder ein Watchface-`.bin` hat: damit lässt sich das
Format und die Frage „signiert oder nicht" konkret beantworten.

---

# 10. Konkretes Modell: ZL99J mit MOY-VYG2-2.0.8

## 10.1 Was das Modell ist

| | |
|---|---|
| Modell | **ZL99J** (FCC ID `2A5HP-ZL99J`, Shenzhen Zhilian Shengya) |
| Display | 2,01" TFT, 240 × 296 |
| MCU | **JieLi JL7012F6** (= AC701N-Familie, Bootloader „br28") |
| Speicher | 64 Mb bzw. 128 MB Flash, 578 KB RAM |
| Sensorik | PPG HX3600 / LC09A, G-Sensor SC7A20 / DA267 |
| Funk | BLE 5.2 |
| App | Da Fit (`com.crrepa.band.dafit`) |
| Firmware | `MOY-VYG2-2.0.8` |

Hinweis: Einzelne Händler-Listings nennen fälschlich „RTL8763EWE" (Realtek) –
das ist Copy-Paste. Die überwiegende Zahl der ODM-Datenblätter (4BS, TVCMALL,
Made-in-China, Glaymertec, Alibaba) nennt **JL7012F6**. Die FCC-Innenfotos
(`2A5HP-ZL99J`, Internal Photos) würden es endgültig bestätigen.

## 10.2 Versionsstring

`MOY-VYG2-2.0.8` → `FirmwareVersionUtils.getFirmwareType()` splittet an `-`
und nimmt Index 1: **`VYG2`**. Das ist die Kompatibilitätsfamilie, nicht der
Chip. `MOY` = Moyoung (Hersteller des Protokolls), `2.0.8` = Version.
Für die Update-Abfrage heißt das: `version=MOY-VYG2-2.0.8`,
`app_version=1.0.2`, plus MAC.

## 10.3 Was das für die Modbarkeit heißt

Der JL7012F6 ist – gemessen an der CRREPA-Chip-Landschaft – **der mod-freundlichste
Fall**. Grund: JieLi ist ein offenerer Hersteller als Goodix/SiFli.

### Ebene 1: Ziffernblätter – gelöst, es gibt fertige Community-Tools

Das Moyoung-v2-Protokoll ist dokumentiert (Gadgetbridge) und deckt sich exakt
mit dem, was ich in der APK gefunden habe (`FE EA 20 0A 31 …`).

* **DaFup** (Python, bleak) – lädt Ziffernblatt- und Hintergrund-`.bin`-Dateien
  auf MoYoung-v2-Uhren.
* **extrathundertool** (Rust) – Encoder/Decoder für Ziffernblatt-Dateien,
  damit lassen sich eigene bauen.
* **DaFbc** – wandelt Hintergrundbilder ins Binärformat.
* **Gadgetbridge** unterstützt Moyoung v2 als Ersatz für die Da-Fit-App
  (Ziffernblatt-Upload macht es selbst nicht, aber Verbinden/Sync/Zeit/Alarme).

Das ist der pragmatische Weg: eigene Dials bauen, per DaFup hochladen, Da Fit
dafür gar nicht mehr nötig.

### Ebene 2: UI-Ressourcen (Icons, Sprache, Layout)

JieLi trennt offiziell zwei Update-Arten:

* **Firmware** → `update.ufw`
* **Ressourcen** → `upgrade.zip` mit `res.ori` im Inneren

Die CRREPA-Bibliothek kann das theoretisch (`CRPUiTransInitiator`,
Kommando `0x6C`), die Da-Fit-Oberfläche nutzt es nicht. Format ist
JieLi-proprietär; ohne ein Referenzpaket vom Hersteller kaum nachzubauen.

### Ebene 3: Firmware

Der DFU-Pfad der App läuft bei diesem Modell über
`com.crrepa.ble.ota.jieli.JieliDfuController` → `q2b`/`lcb`-Dateitransfer.

Was für JL7012F6/AC701N öffentlich existiert:

* `Jieli-Tech/fw-Bootloader` – Bootloader-Quellcode, Tabelle nennt AC701N → `br28`
* `Jieli-Tech/Android-JL_OTA`, `iOS-JL_OTA` – offizielles RCSP-OTA-SDK
* `Jieli-Tech/fw-AC63_BT_SDK` – SDK-Quellen (AC701N-SDK über Distributoren)
* `kagaimiq/jl-uboot-tool` – Flasher/Dumper, unterstützt **AC701N** (BR28)
* Quarkslab-Artikel – haben eine JieLi-Uhr-Firmware extrahiert, das
  „neue Firmware-Format" und die Ressourcen-Scrambling reverse-engineered

Damit ist ein **eigenes Firmware-Image prinzipiell möglich** – aber es ist ein
Wochen/Monate-Projekt: Board-Konfiguration (LCD 240×296, Touch CST820,
PPG HX3600, ACC SC7A20, Flash-Größe, Pinmux), JieLi-Toolchain, UI-Framework.
Ein „Custom OS" ist nicht realistisch, ein gepatchtes Stock-Image eher.

Wichtig: Die JieLi-OTA hat eine **Session-Authentifizierung** (RCSP/HashPair),
das ist aber keine Signatur des Images. Ob der br28-Bootloader ein Image
kryptografisch prüft, ist konfigurierbar und vom Hersteller abhängig – das
sieht man erst am konkreten `update.ufw`.

### Ebene 4: Hardware-Zugang

Wenn OTA nicht reicht: JL-Chips lassen sich über UART/USB in den
Upgrade-Modus zwingen (Forced-Download-Tool, oder Flash-MISO beim Boot nach
GND). Dafür muss die Uhr auf – bei diesem Gehäuse (IP68, verklebt) ist das
der unangenehmste Schritt.

## 10.4 Woher das Firmware-Paket kommt

1. **Aus der App heraus**: Update-Check auslösen, Traffic mitschneiden
   (mitmproxy + eigenes CA-Zertifikat), URL + MD5 aus der Antwort ziehen.
2. **Über die Community-Route**: Für Moyoung-Uhren ist dokumentiert, dass
   `crrepa.com` auf Anfrage mit Modelltyp, Version und MAC die passende
   Firmware herausgibt (siehe atc1441/DaFlasherFiles, Issue #7).
3. Danach: Container analysieren (Header, Magic, Längen, evtl. Scrambling),
   erst dann an Modifikation denken.

## 10.5 Risiko-Einschätzung für genau dieses Modell

| Vorhaben | Aussicht | Anmerkung |
|---|---|---|
| Eigenes Ziffernblatt | **sehr hoch** | DaFup + extrathundertool, Community-erprobt |
| Da Fit durch Gadgetbridge ersetzen | hoch | Moyoung v2 wird unterstützt |
| UI-Ressourcen tauschen | mittel | JieLi `res.ori`, Format unbekannt |
| Firmware patchen | mittel-niedrig | Bootloader-Frage, Toolchain nötig |
| Komplett eigenes OS | sehr niedrig | Monate Arbeit, kein Nutzen |

**Brick-Warnung:** Ob die Uhr Single- oder Dual-Bank-OTA nutzt, ist von außen
nicht sichtbar. Bei Single-Bank macht ein Abbruch mitten im Flash das Gerät
unbrauchbar; bei Dual-Bank bleibt die alte Partition als Fallback. Ohne
Hardware-Backup (jl-uboot-tool über UART) gibt es keinen Rettungsweg.
