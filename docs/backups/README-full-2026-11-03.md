# Vollbackup beider Drucker, 2026-11-03

Kompletter Inhalt von `~/printer_data/config` beider Drucker, geholt per
`tar -czh` (Symlinks aufgeloest: `calibrate_macros.cfg`, `mainsail.cfg`,
`print_area_bed_mesh.cfg`, `timelapse.cfg` sind auf dem Pi Links in fremde Repos,
hier liegen sie als echte Dateien). Enthalten sind auch Klippers
`printer-<datum>.cfg`-Rotationen, alle `*.bak-*`, der aeltere Sync vom
2026-09-05 unter `backups/` und die versteckten Ergebnisdateien
`.offset_*_results.json`.

| Ordner | Drucker | IP | Stand |
|---|---|---|---|
| `350-full-2026-11-03/` | 350er (T0-T5) | 192.168.178.114 | nach Entfernen des BED_MESH_CLEAR in PRINT_START, Klipper neu geladen |
| `250-full-2026-11-03/` | 250er (T0-T3) | 192.168.178.60 | nach Entfernen des BED_MESH_CLEAR in PRINT_START, Klipper neu geladen |

Rueckspielen: Dateien einzeln per scp an die gleiche Stelle, die vier Symlinks
dabei NICHT ueberschreiben (sonst landet die Datei im fremden Repo, siehe
install.sh-Symlink-Falle). Die Live-Configs auf dem Pi bleiben die Wahrheit,
`configs/250` und `configs/350` im Repo sind nur ein Spiegel.
