# No Roads Mission Maker

Berechnet Fußrouten auf OpenStreetMap-Daten (`.osm.pbf`), die Straßen so weit wie möglich meiden:
über Pfade, Feldwege, Radwege und – wo es nicht anders geht – so wenig Straße wie nötig.
Zugangsbeschränkungen (private Wege, Schranken, Tunnel …) werden nicht gewichtet, sondern nur
für die fertige Route protokolliert.

Die Oberfläche ist im Stil von Frutiger Aero gehalten: Liquid-Glass-Flächen, Gel-Schalter und ein Pfadanteil-Messer.

## Funktionen

- **PBF laden**, optional auf einen Ausschnitt (bbox) begrenzt; wird als Cache abgelegt, das zweite Laden geht schnell.
- **Start und Ziel** per Klick auf die Karte oder als Koordinaten.
- **Straßenarm:** jede der 12 Wegklassen hat einen eigenen Faktor (Regler, Zahl, Sperren).
- **Pfade maximieren:** Pfade kosten 1, alles andere ein Vielfaches K davon.
- **Vergleich:** durchläuft mehrere K-Werte und zeigt Umweg gegen Pfadanteil.
- **GPX-Korridor:** bleibt innerhalb einer wählbaren Breite um einen vorhandenen Track.
- **Wegnetz anpassen:** ausgeschlossene Straßentypen, Abstand und Anteil der Parallel-Erkennung.
- **Export:** GPX der Route und der kürzesten Route, Zugangsprotokoll (CSV), Zusammenfassung (JSON), Kartenbild (PNG).
- **Kommandozeile:** `nrmm-cli route|sweep|info` für alles davon.

## Windows-EXE

Die EXE wird per GitHub Actions auf `windows-latest` gebaut (siehe `.github/workflows/build.yml`).
Unter *Actions → Windows-EXE → Artifacts* liegt `NoRoadsMissionMaker-windows`.
Lokal unter Windows: `powershell -File packaging/build_exe.ps1`.

Die Oberfläche läuft in einem eigenen Fenster (WebView2, bei Windows 10/11 vorhanden).
Fehlt es, öffnet sich stattdessen der Standardbrowser.

## Android-App

`android/` enthält ein Gradle-Projekt (Chaquopy): dieselbe Python-Logik und dieselbe Oberfläche laufen in einer WebView.
Der Workflow `Android-APK` baut `app-debug.apk` (Artifact `NoRoadsMissionMaker-android`); auf dem Handy installieren
(„Unbekannte Apps zulassen“). Kartendateien über „Wählen“ aus dem Speicher holen, Ergebnisse über „Ordner öffnen“ teilen.
Große `.pbf`-Dateien sprengen den Speicher des Handys: besser einen Ausschnitt (z. B. mit `osmium extract`) verwenden.

## Entwicklung

```
pip install -e ".[gui,dev]"
python -m pytest
python -m nrmm            # Oberfläche
nrmm-cli info datei.pbf
```

## Grenzen

- Das Einlesen ist reines Python/numpy: ca. 1 Minute für 160 MB, große Länderdateien brauchen länger (danach Cache).
  Besser einen Ausschnitt verwenden.
- Weglose Querfeldeinstrecken, Flächen und Schutzgebiete werden nicht berechnet.
- Daten von OpenStreetMap-Mitwirkenden (ODbL).
