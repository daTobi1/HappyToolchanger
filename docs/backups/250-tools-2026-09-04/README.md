# Sicherung der Tool-Offsets des 250ers vor der Eddy-XY-Übernahme (2026-09-04)

Stand der vier `T<n>.cfg` auf dem Drucker (`~/printer_data/config/toolchanger/tools/`),
gezogen am 2026-09-04 abends, **bevor** irgendein Eddy-Ergebnis übernommen wurde.
Die Dateien hier sind 1:1-Kopien. Auf dem Drucker liegt zusätzlich eine Kopie unter
`~/printer_data/config/toolchanger/tools.bak-2026-09-04-vor-eddy-xy/`.
`configs/250/toolchanger/tools/` im Repo hatte zu diesem Zeitpunkt dieselben Werte.

| Tool | gcode_x_offset | gcode_y_offset | gcode_z_offset | Tap-Probe z_offset |
|---|---|---|---|---|
| T0 | 0.000 | 0.000 | 0.000000 | 0 |
| T1 | 0.33 | -5.05 | -0.055 | -0.644 |
| T2 | 0.44 | -4.56 | 0.103 | -0.808 |
| T3 | -0.18 | -5.84 | -0.183 | -0.466 |

Die gcode-Offsets X/Y stammen aus der Kamera-Kalibrierung, Z aus dem Z-Switch.
Live per API bestätigt (`tool T<n>.gcode_x/y/z_offset`) mit denselben Werten.

Zurückspielen: Datei(en) nach `~/printer_data/config/toolchanger/tools/` kopieren,
dann `RESTART`.
