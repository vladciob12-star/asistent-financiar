# Asistent financiar personal

Nucleu local de import și registru financiar în dezvoltare. Datele rămân local; proiectul nu se conectează implicit la servicii externe și nu conține date financiare reale.

## Funcții disponibile

- Import Creasoft CSV cu antete validate și totaluri per automat. Raportul este păstrat ca agregat local; fără dată/perioadă explicită nu se deduce o perioadă.
- Agregatele Creasoft pot fi scrise în SQLite cu `--import-creasoft-report`; aceleași bytes nu se importă din nou, etichetele necunoscute cer verificare, iar încasările cash + non-cash nu sunt prezentate ca numerar fizic.
- Import manual MT940 pentru extrase BT Go, fără mapare; pentru CSV/XLSX rămâne disponibilă maparea explicită. Tranzacțiile nu sunt clasificate automat.
- Import CSV financiar Booking.com cu antete localizate, câmpuri brute/comision/taxe/net/plată separate, deduplicare de fișier și referințe rezervare pseudonimizate.
- Import automat opțional al rapoartelor Booking CSV din Gmail read-only; dezactivat până la autorizarea și configurarea OAuth Google.
- Registru SQLite local pentru observații, importuri, propuneri de reconciliere și raport HTML lunar; veniturile, cheltuielile și rezultatul estimat sunt separate pe monedă, fără conversii valutare implicite.
- O plată Booking raportată rămâne neconfirmată în bancă până la potrivire și confirmare manuală. Ea nu este adunată la fondurile bancare.
- Reimportul aceluiași fișier este idempotent. Înregistrările similare necerte sunt păstrate și semnalate.
- Profil Docker/Render cu autentificare cloud obligatorie, health check și cale de bază de date configurabilă.
- Snapshot-uri Render automate zilnice și criptate pentru discul persistent; utilitar separat pentru backup/restaurare SQLite AES-256-GCM, rulat la comandă. Nu este configurată o destinație externă automată.
- Integrare pregătită pentru Meta WhatsApp Cloud API: verificare challenge și HMAC, expeditori autorizați, deduplicare și răspunsuri doar agregate. Dezactivată până la configurarea secretelor Meta.
- Rapoartele PDF pot fi trimise numai ca răspuns la cererea explicită a expeditorului autorizat, în fereastra de 24 de ore; nu există trimitere programată.

## Comenzi locale

Pornește interfața locală de import și raportare:

```text
python web_app.py --database data/finante.sqlite3
```

Deschide `http://127.0.0.1:8765` în browser. Aplicația ascultă numai pe calculatorul local. În interfață poți încărca rapoarte Creasoft CSV, rapoarte financiare Booking CSV și extrase bancare BT Go MT940 (`.sta`, `.940`, `.mt940`); CSV/XLSX bancare folosesc o mapare JSON explicită. Fișierele originale sunt procesate local și nu sunt păstrate; registrul SQLite salvează datele normalizate, amprenta fișierului și proveniența rândurilor. Potrivirile sunt propuneri până la confirmarea manuală.

Tabloul de bord însumează veniturile, cheltuielile și rezultatul estimat separat pe monede, fără conversii. Banii disponibili apar numai după introducerea manuală a unui sold și a datei din extras. Raportul lunar PDF se descarcă din interfață și este generat temporar pe dispozitiv.

Protecția importului manual: încărcările sunt limitate la 10 MB, procesate local într-un director temporar și validate cu token CSRF și verificarea originii. Serverul ascultă numai pe `127.0.0.1` și nu loghează valorile financiare ori căile cererilor. Registrul păstrează câmpurile normalizate necesare și hash-ul fișierului pentru detectarea reimportului; baza SQLite nu este criptată de aplicație, deci păstreaz-o într-un cont și pe un dispozitiv protejat.

Creasoft nu intră în totalurile lunare dacă raportul nu oferă perioada; valorile cash + non-cash nu sunt sold de numerar. Pentru Booking, payout-ul se confirmă doar după o asociere cu înregistrarea bancară. Clasifică manual tranzacțiile neîncadrate din tabelul interfeței.

## Pregătire cloud (neactivată)

Comparativul de costuri, profilul Render, condițiile WhatsApp/Creasoft/Booking și pașii de activare sunt în [docs/cloud-readiness.md](docs/cloud-readiness.md). render.yaml este doar un blueprint; nu a fost creat niciun serviciu cloud. Discul persistent trebuie montat la /var/data. Publicarea pe internet cere APP_PASSWORD_HASH, APP_PASSWORD_SALT și APP_PUBLIC_URL HTTPS; bind-ul public fără ele eșuează intenționat.

Pentru planul de 0 lei/lună și configurația OCI Always Free neactivată, vezi [docs/oracle-always-free.md](docs/oracle-always-free.md) și fișierele din [deploy/oracle](deploy/oracle). Configurația nu creează resurse sau conturi.

Pentru a configura hash-ul parolei în mod interactiv:

    python web_app.py --hash-password

Păstrează ambele valori afișate ca secrete în furnizorul cloud și adaugă URL-ul HTTPS final. Nu le comite în Git.

## WhatsApp Business (neconectat)

Implementarea folosește direct Meta WhatsApp Cloud API, fără BSP. Pentru configurare cere un webhook HTTPS la `/webhook/whatsapp`, token de verificare, App Secret, access token, Phone Number ID, versiunea Graph API și allowlist-ul propriilor numere. Toate valorile sunt secrete în mediul serviciului și nu sunt comise. `WHATSAPP_ENABLE_MESSAGING=false` și `WHATSAPP_ALLOW_REPORTS=false` rămân valorile implicite; integrarea nu trimite și nu primește mesaje până la activarea explicită.

Comenzi acceptate: `/ajutor`, întrebări despre venituri/cheltuieli/profit estimat/sold disponibil și `/raport AAAA-LL`. Răspunsurile sunt calcule deterministe din agregate SQLite, fără model AI, fără descrieri de tranzacții și fără conversii valutare. Raportul PDF se livrează doar la cerere, într-un răspuns la mesajul primit în ultimele 24 de ore. Trimiterile automate în afara ferestrei nu sunt implementate; Meta cere șabloane aprobate pentru inițierea sau reluarea conversației și acestea pot avea cost.

Webhook-ul procesează doar mesaje text. Imaginile/documentele trimise pe WhatsApp sunt ignorate; extrasele bancare continuă să fie importate numai manual prin aplicație. Mesajele brute, numerele de telefon și conținutul financiar al webhook-ului nu se salvează; SQLite păstrează ID-uri de mesaj pentru deduplicarea livrărilor repetate.

## Telegram (implementat, neconectat)

Endpointul separat `/webhook/telegram` folosește Telegram Bot API prin webhook HTTPS. Integrarea este dezactivată până la configurarea `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_ALLOWED_USER_IDS` și `TELEGRAM_ENABLE_MESSAGING=true`. Secretul webhook-ului este verificat în antetul `X-Telegram-Bot-Api-Secret-Token`; conversațiile de grup și orice ID numeric care nu este în allowlist sunt ignorate fără răspuns financiar. Nu se folosesc numele de utilizator pentru autorizare.

`TELEGRAM_CONFIGURE_WEBHOOK_ON_START=true` cere aplicației să înregistreze automat callback-ul HTTPS din `APP_PUBLIC_URL` la pornire. `TELEGRAM_ALLOW_REPORTS=true` permite trimiterea unui PDF numai la comanda explicită `/raport AAAA-LL` sau „Trimite-mi raportul financiar lunar”. ID-urile update-urilor sunt deduplicate în SQLite; textul mesajelor, numele și ID-urile chatului nu se păstrează.

Răspunsurile refolosesc agregatele SQLite și raportează clar când lipsesc date. Răspunsurile financiare includ momentul ultimei importări/salvări de sold (UTC), iar interogările despre ERA și Crișul identifică aparatele configurate. Vânzările Creasoft fără perioadă sunt descrise drept vânzări raportate, nu numerar disponibil în tonomat. Booking neîncasat rămâne separat de banii disponibili. Telegram primește momentan Booking CSV, Creasoft CSV, MT940 și CSV/XLSX bancar numai dacă se potrivește mapării explicite existente DATA/DESCRIERE/SUMA în RON. Botul prezintă un rezumat și cere confirmare înainte de import; originalul este temporar, iar draftul expiră în 30 de minute. PDF-urile, fotografiile și documentele fără parser validat sunt respinse; nu folosim OCR sau servicii AI externe. După import, tranzacțiile bancare rămân neclasificate, totalurile Creasoft cash+non-cash nu stabilesc numerarul fizic, iar creanțele Booking sunt separate. Botul trebuie creat de proprietar prin @BotFather; tokenul și ID-ul numeric al proprietarului nu se publică și nu se comit. Codul nu pretinde că botul este conectat până când tokenul, ID-ul autorizat și webhook-ul nu sunt configurate și testate efectiv.

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

Creasoft importă în SQLite agregate, nu tranzacții pe zile; dacă exportul nu declară perioada, rândurile rămân în afara totalurilor lunare. Booking parserul acceptă formatul financiar localizat testat, dar încă nu suportă toate variantele/monedele/exporturile XLSX. Importul bancar MT940/CSV/XLSX este manual; PDF/OCR nu este suportat, iar tranzacțiile rămân neclasificate până la revizuire. Creasoft API, Booking API și citirea automată a e-mailului nu sunt conectate. WhatsApp, Creasoft API și Booking API nu sunt conectate. Hostingul este doar pregătit, nu activat. Snapshot-urile Render sunt automate în cazul unui deploy cu disc persistent; nu este configurată o copie criptată off-site.

Rapoartele și fișierele financiare locale sunt excluse din Git prin `.gitignore`. Nu folosi date demonstrative drept tranzacții reale.


