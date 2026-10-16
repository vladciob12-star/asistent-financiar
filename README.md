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
- Profil Docker/Render cu autentificare cloud obligatorie, health check și cale de bază de date configurabilă.
- Snapshot-uri Render automate zilnice și criptate pentru discul persistent; utilitar separat pentru backup/restaurare SQLite AES-256-GCM, rulat la comandă. Nu este configurată o destinație externă automată.

## Comenzi locale

Pornește interfața locală de import și raportare:

```text
python web_app.py --database data/finante.sqlite3
```

Deschide `http://127.0.0.1:8765` în browser. Aplicația ascultă numai pe calculatorul local. În interfață poți încărca rapoarte Creasoft CSV, rapoarte financiare Booking CSV și extrase bancare CSV/XLSX cu o mapare JSON explicită. Fișierele originale sunt procesate local și nu sunt păstrate; registrul SQLite salvează datele normalizate, amprenta fișierului și proveniența rândurilor. Potrivirile sunt propuneri până la confirmarea manuală.

Tabloul de bord însumează veniturile, cheltuielile și rezultatul estimat separat pe monede, fără conversii. Banii disponibili apar numai după introducerea manuală a unui sold și a datei din extras. Raportul lunar PDF se descarcă din interfață și este generat temporar pe dispozitiv.

Protecția importului manual: încărcările sunt limitate la 10 MB, procesate local într-un director temporar și validate cu token CSRF și verificarea originii. Serverul ascultă numai pe `127.0.0.1` și nu loghează valorile financiare ori căile cererilor. Registrul păstrează câmpurile normalizate necesare și hash-ul fișierului pentru detectarea reimportului; baza SQLite nu este criptată de aplicație, deci păstreaz-o într-un cont și pe un dispozitiv protejat.

Creasoft nu intră în totalurile lunare dacă raportul nu oferă perioada; valorile cash + non-cash nu sunt sold de numerar. Pentru Booking, payout-ul se confirmă doar după o asociere cu înregistrarea bancară. Clasifică manual tranzacțiile neîncadrate din tabelul interfeței.

## Pregătire cloud (neactivată)

Comparativul de costuri, profilul Render, condițiile WhatsApp/Creasoft/Booking și pașii de activare sunt în [docs/cloud-readiness.md](docs/cloud-readiness.md). render.yaml este doar un blueprint; nu a fost creat niciun serviciu cloud. Discul persistent trebuie montat la /var/data. Publicarea pe internet cere APP_PASSWORD_HASH, APP_PASSWORD_SALT și APP_PUBLIC_URL HTTPS; bind-ul public fără ele eșuează intenționat.

Pentru a configura hash-ul parolei în mod interactiv:

    python web_app.py --hash-password

Păstrează ambele valori afișate ca secrete în furnizorul cloud și adaugă URL-ul HTTPS final. Nu le comite în Git.

Exemplu de backup criptat, rulat pe instanța care poate accesa baza:

    python scripts/backup_database.py --create --database /var/data/finante.sqlite3 --backup /var/data/backup-2026-10-16.afpb

BACKUP_ENCRYPTION_KEY trebuie să fie Base64 pentru o cheie aleatorie de 32 bytes, păstrată separat. Restaurarea cere oprirea aplicației și o cale --target explicită. În hostingul Render, snapshot-urile discului sunt zilnice și automate. Backup-ul criptat portabil nu are programare sau transfer extern în această versiune.

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

Creasoft importă în SQLite agregate, nu tranzacții pe zile; dacă exportul nu declară perioada, rândurile rămân în afara totalurilor lunare. Booking parserul acceptă formatul financiar localizat testat, dar încă nu suportă toate variantele/monedele/exporturile XLSX. Importul bancar necesită mapare și nu suportă PDF/OCR ori clasificare automată. Nu există webhook WhatsApp sau API Creasoft/Booking conectate. Hostingul este doar pregătit, nu activat. Snapshot-urile Render sunt automate în cazul unui deploy cu disc persistent; nu este configurată o copie criptată off-site.

Rapoartele și fișierele financiare locale sunt excluse din Git prin `.gitignore`. Nu folosi date demonstrative drept tranzacții reale.
