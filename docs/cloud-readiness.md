# Găzduire cloud și integrări (evaluare 16 octombrie 2026)

Această configurație este pregătită pentru revizuire, dar nu a fost aplicată într-un cont cloud. Nu a fost creat niciun serviciu sau cont, nu au fost introduse date financiare și nu a fost conectată banca.

## Costuri orientative de găzduire

Estimări pentru o singură instanță mică, permanent pornită, un disc de 1 GB și trafic redus. Nu includ TVA, domeniu propriu, transfer peste cote, copii externe ori costuri de conversie valutară.

| Opțiune | Estimare lunară | Persistență și protecție | Efort/limitări |
| --- | ---: | --- | --- |
| Render Starter + disc 1 GB | aproximativ **$7.25/lună** ($7 compute + $0.25 disc) | Discul și snapshot-urile automate sunt criptate în repaus; HTTPS/TLS gestionat de platformă. Serviciul gratuit nu oferă disc persistent și poate intra în repaus. | Cel mai simplu profil pregătit aici. Discul este atașat unei singure instanțe; nu se poate scala pe mai multe instanțe. |
| Fly.io shared CPU 1x, 512 MB + volum 1 GB | aproximativ **$4.54/lună**, plus trafic | Presetul shared-cpu-2x de 512 MB costă $4.39/lună; volumul de 1 GB adaugă $0.15. Snapshots: primii 10 GB/lună gratuit, apoi tarif separat. | Necesită configurarea și operarea volumului/snapshot-urilor; egress-ul și regiunea pot schimba factura. |
| Hetzner CX23 + IPv4 | aproximativ **€5.99/lună fără TVA** (€5.49 VM + €0.50 IPv4) | Stocarea VM este persistentă; configurația trebuie să adauge criptarea volumului și backup-uri verificate. | Ieftin și suficient ca resurse, dar utilizatorul administrează OS-ul, firewall-ul, actualizările, criptarea și recuperarea. Backup-urile Hetzner sunt suplimentare. |

**Recomandare pentru prima instalare:** Render Frankfurt, cu plan Starter și discul persistent de 1 GB din render.yaml. Are cost fix simplu, TLS administrat și criptare documentată pentru disc/snapshot. Este în continuare o opțiune cu plată: YAML-ul doar descrie resursa; nu am creat-o și nu am lansat un deploy. Pentru cost minim și control de infrastructură, Hetzner rămâne o alternativă, dar necesită mai multă operare de securitate.

Prețurile sunt instantaneul paginilor oficiale la data de mai sus și se pot schimba. Reconfirmă-le în calculatorul/factura furnizorului înainte de orice activare.

## Configurație cloud pregătită

- Dockerfile: imagine Python, procesul aplicației rulează sub UID neprivilegiat, iar entrypoint-ul setează drepturile pe discul montat.
- render.yaml: profil Frankfurt, plan plătit explicit, disc persistent de 1 GB, deploy automat dezactivat, health check și secrete cerute separat. Nu conține valori reale de secrete.
- Aplicația acceptă DATABASE_PATH, APP_HOST și PORT; endpointul /healthz verifică SQLite fără a expune conținutul bazei.
- Orice bind public (0.0.0.0) este refuzat fără hash PBKDF2, salt, APP_PUBLIC_URL HTTPS și cookie Secure. Interfața de import este protejată de autentificare, cookie HttpOnly/SameSite și sesiune de 12 ore. În modul local implicit rămâne legată la 127.0.0.1.
- SQLite este salvat sub /var/data, în discul persistent. Pe Render, conținutul din afara punctului de montare rămâne efemer; această aplicație folosește baza exact din punctul persistent.
- Render creează automat snapshot-uri zilnice criptate ale discului, disponibile cel puțin 7 zile; nu este necesară o programare suplimentară pentru această protecție. Restaurarea revine la starea întregului disc și poate pierde modificările de după snapshot. Acestea nu sunt copii independente în alt cont/furnizor. Pentru o copie portabilă, scripts/backup_database.py creează la comandă un backup SQLite consistent și criptat AES-256-GCM. Cheia Base64 de 32 bytes trebuie păstrată separat; aplicația nu o generează și nu o stochează.
- Nu este configurat un backup extern automat. Pentru protecție independentă de Render trebuie aleasă o destinație, frecvență, retenție și custodia cheii; aceasta poate adăuga costuri și cere aprobarea proprietarului.

Pași care vor necesita decizia/configurarea proprietarului:

1. Alege contul de găzduire, regiunea, domeniul/URL-ul public și bugetul lunar maxim.
2. Generează parola: python web_app.py --hash-password; setează APP_PASSWORD_HASH, APP_PASSWORD_SALT și URL-ul final HTTPS în secretele serviciului.
3. Revizuiește render.yaml, apoi creează manual serviciul în cont. În acel moment începe costul planului și discului.
4. Testează login, importul unui fișier sintetic, repornirea/redeploy-ul și descărcarea raportului.
5. Stabilește destinația backup-ului, stochează separat cheia, programează copii și efectuează un test de restaurare înainte de a folosi date reale.

## Integrări și condiții de activare

| Integrare | Poate fi automatizată după configurare | Necesită de la proprietar / necesită aprobare |
| --- | --- | --- |
| Extrase bancare | Nimic nu trebuie conectat. Deduplicarea și reconcilierea rulează după ce proprietarul încarcă fișierul. | **Rămân exclusiv import manual.** Nu se configurează Open Banking/PSD2, acreditări de bancă, scraper sau sync automat. |
| WhatsApp Business Platform | După webhook public HTTPS, aplicația poate verifica semnătura Meta, accepta doar expeditori autorizați și prelucra o comandă permisă. | Cont Meta Business/developer, WABA, număr de telefon, token de sistem, webhook și permisiuni. Utilizatorul trebuie să aprobe explicit ce mesaje/date se acceptă și dacă se trimit mesaje. Meta taxează mesajele livrate în funcție de piață și categorie; tariful actual trebuie verificat în rate card pentru România înainte de a activa trimiterea. Nu folosim furnizor BSP terț în acest plan. |
| Creasoft | O integrare read-only ar putea prelua sumarul vending dacă produsul și contul confirmă un endpoint/API și acces autorizat. Importul CSV existent poate rămâne fallback. | API-ul gratuit prezentat public de Creasoft este descris pentru integrare payroll/ERP și sincronizare angajați/evenimente/sumarizări. Pagina menționează separat produsul de telemetrie vending, dar nu publică un contract API de rapoarte financiare vending. Cere Creasoft: endpoint și schemă pentru contul tău, permisiuni read-only, intervale, cost/contract, perioadă și semantica sumelor cash/non-cash. Nu presupunem că API-ul payroll acoperă vending. |
| Booking.com | Cu acces API autorizat, se pot prelua periodic rapoarte de reconciliere/payout pentru proprietățile conectate, apoi pot fi păstrate ca observații și deduplicate. | Booking Connectivity Partner onboarding, cont machine/token, conectarea proprietății și acces explicit la endpoint. Payments API este disponibil numai pentru proprietăți care folosesc Payments by Booking.com și au implementat Reservations API. Aprobările Booking și accesul contului sunt necesare. Rapoartele API rămân dovezi de payout; ele nu confirmă mișcarea din contul bancar. |

## Separarea responsabilităților

**Automat în versiunea pregătită:** păstrarea SQLite pe disc persistent, autentificarea, health check, deduplicarea fișierelor deja încărcate, generarea locală a raportului, criptarea unei copii la comanda proprietarului și testele sintetice.

**Automat doar după aprobări/configurare:** polling read-only Creasoft/Booking, webhook WhatsApp, programarea copiilor externe și orice mesaj WhatsApp trimis. Importurile API ar trebui să rămână în stare de revizuire; nu clasificăm automat tranzacții și nu confirmăm reconcilierea financiară fără acțiunea proprietarului.

**Necesită decizia proprietarului:** buget și furnizor cloud, domeniu/secrete, unde sunt stocate backup-urile și cine le poate decripta, autorizarea conectării la fiecare cont terț, lista expeditorilor WhatsApp autorizați, tipurile de rapoarte permise și orice cheltuială Meta/BSP/cloud.

## Referințe oficiale

- [Render pricing](https://render.com/pricing) — compute, disc persistent și lățime de bandă.
- [Render persistent disks](https://render.com/docs/disks) — persistență, criptare și snapshot-uri.
- [Render free limits](https://render.com/docs/free) — limitele planului gratuit.
- [Fly.io pricing (actualizare efectivă 1 octombrie 2026)](https://fly.io/pricing-update/) — compute, volume, snapshot-uri, trafic și TLS.
- [Hetzner pricing adjustment, 15 iunie 2026](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/) — CX23 și IPv4.
- [Meta WhatsApp Business Platform pricing](https://whatsappbusiness.com/products/platform-pricing/) — prețurile per mesaj livrat, piață și categorie.
- [Meta WhatsApp Cloud API getting started](https://developers.facebook.com/docs/whatsapp/cloud-api/get-started).
- [Creasoft: integrare/API și export Excel](https://www.creasoft.ro/ro/control-access-made-in-romania).
- [Booking.com Connectivity APIs](https://developers.booking.com/connectivity/docs), [autentificare](https://developers.booking.com/connectivity/docs/authentication), [acces endpoint](https://developers.booking.com/connectivity/docs/managing-endpoint-access), [Payments API](https://developers.booking.com/connectivity/docs/payments-api/understanding-the-payments-api) și [Reconciliation API](https://developers.booking.com/connectivity/docs/openapispecs/reconciliation-api/reconciliation-api-specification).
