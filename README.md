# Asistent financiar personal

Nucleu local de import și registru financiar în dezvoltare. Datele rămân local; proiectul nu se conectează implicit la servicii externe și nu conține date financiare reale.

## Funcții disponibile

- Import Creasoft CSV cu antete validate și totaluri per automat. Raportul este păstrat ca agregat local; fără dată/perioadă explicită nu se deduce o perioadă.
- Agregatele Creasoft pot fi scrise în SQLite cu `--import-creasoft-report`; aceleași bytes nu se importă din nou, etichetele necunoscute cer verificare, iar încasările cash + non-cash nu sunt prezentate ca numerar fizic.
- Import CSV/XLSX pentru extrase și formate mapate explicit. Maparea cere antete, câmp de dată, sumă și monedă; tipul și activitatea pot rămâne neclasificate.
- Import CSV financiar Booking.com cu antete localizate, câmpuri brute/comision/taxe/net/plată separate, deduplicare de fișier și referințe rezervare pseudonimizate.
- Registru SQLite local pentru observații, importuri, propuneri de reconciliere și raport HTML lunar; veniturile, cheltuielile și rezultatul estimat sunt separate pe monedă, fără conversii valutare implicite.
- O plată Booking raportată rămâne neconfirmată în bancă până la potrivire și confirmare manuală. Ea nu este adunată la fondurile bancare.
- Reimportul aceluiași fișier este idempotent. Înregistrările similare necerte sunt păstrate și semnalate.

## Comenzi locale

Importul Booking specializat:

```text
python main.py --import-booking-report raport-booking.csv --database data/finante.sqlite3
```

Importul agregat Creasoft:

```text
python main.py --import-creasoft-report raport-creasoft.csv --database data/finante.sqlite3
```

Import CSV/XLSX cu mapare explicită și registru opțional:

```text
python main.py --import-file extras.xlsx --mapping config/csv-mapping.template.json --export outputs/registru.csv --database data/finante.sqlite3
```

Șablonul de mapare este gol și trebuie completat cu antetele reale ale exportului. Importul XLSX folosește valori calculate salvate în workbook.

Generare raport HTML lunar:

```text
python main.py --report-month 2026-09 --database data/finante.sqlite3 --report-html outputs/raport-lunar.html
```

Rulare teste:

```text
python -m unittest discover -s tests -v
```

## Limite și pașii rămași

Creasoft importă în SQLite agregate, nu tranzacții pe zile; dacă exportul nu declară perioada, rândurile rămân în afara totalurilor lunare. Booking parserul acceptă formatul financiar localizat testat, dar încă nu suportă toate variantele/monedele/exporturile XLSX. Importul bancar necesită mapare și nu suportă PDF/OCR ori clasificare automată. Nu există webhook WhatsApp, autorizare expeditor, găzduire cloud, sincronizare API, backup sau programare automată a raportului. Aceste integrări nu sunt active.

Rapoartele și fișierele financiare locale sunt excluse din Git prin `.gitignore`. Nu folosi date demonstrative drept tranzacții reale.
