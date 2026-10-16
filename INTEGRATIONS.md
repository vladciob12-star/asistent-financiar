# Pregătirea integrărilor

## Ce funcționează acum

- **Creasoft vending:** importul local folosește antetele din prototipul arhivei și agregă vânzările pe vendomat. Exportul folosit în producție trebuie comparat cu schema reală din cont înainte de a considera reconcilierea validată.
- Agregatul CSV poate fi importat în SQLite cu `python main.py --import-creasoft-report raport.csv --database data/finante.sqlite3`. Sunt mapate doar aliasurile explicite pentru Oradea Era și Crișul Shopping Center Oradea; etichetele necunoscute rămân `unassigned` pentru verificare. Importul păstrează suma combinată cash + non-cash ca vânzări raportate, nu ca sold de numerar fizic. Dacă perioada nu este prezentă, raportul apare separat ca perioadă necunoscută și nu intră în totalurile lunare.
- **Extrase bancare și exporturi Booking.com:** `financial_imports.py` importă CSV/XLSX numai după o mapare explicită a antetelor, datei, monedei și separatorului. CLI poate salva registrul normalizat CSV și, opțional, observațiile într-o bază SQLite locală cu proveniența fișier/rând. Fără mapări explicite de categorie/stare, înregistrările rămân neclasificate. Totalurile lunare CSV sunt separate pe monedă.
- **Raport financiar Booking CSV localizat:** `booking_imports.py` recunoaște antetele efectiv examinate, citește separat valoarea rezervării, comisionul, taxele, suma netă și plata, iar referința rezervării este stocată numai ca hash. Importul se face cu `python main.py --import-booking-report raport.csv --database data/finante.sqlite3`. Rândurile payout sunt creanțe raportate, nu încasări bancare. Potrivirea cu o tranzacție bancară este doar propunere până la confirmarea manuală. Fișierul poate include exporturi după versiune și localizare; exporturi cu alte antete sunt respinse intenționat și trebuie adăugate cu teste.
- Datele nu sunt trimise nicăieri. Importurile nu deduc monede, date, categorii, proprietăți sau semantica unei sume.

Exemplu de utilizare după completarea mapării cu antetele unui export real:

```powershell
python main.py --import-file cale\extras.xlsx --mapping config\mapare.json --export outputs\registru.csv --monthly-export outputs\totaluri-lunare.csv --database data\finante.sqlite3
```

`config/csv-mapping.template.json` este doar o structură goală, nu o mapare Booking.com sau bancară și nu conține date de exemplu. Se copiază, apoi se completează folosind antetele și formatul observate în fișierul real. Exporturile pot conține date personale; păstrează-le local și exclude-le din Git.

## Limitele integrărilor directe

- **Creasoft:** documentația oficială disponibilă descrie exporturi Excel de rapoarte financiare cu echipament, cantitate vândută, încasări reale și numerar colectat. Nu demonstrează însă un API public pentru sincronizare vending. Până când Creasoft confirmă accesul API pentru produsul și contul titularului, Excel/CSV exportat este alternativa sigură. Antetele și perioada trebuie validate pe un export real, anonimizat; documentul public este vechi și nu garantează schema contului actual.
- **Booking.com:** Connectivity API este pentru Connectivity Partners. Accesul cere machine account cu autentificare token-based, proprietatea legată la cont, accesul endpointului, tipul de conexiune potrivit și eventual certificarea cerută. Autentificarea credential-based a fost retrasă la 31 decembrie 2025. Nu este o integrare generică activă pentru un cont de cazare; până la acordarea acestor permisiuni, importul rapoartelor financiare exportate rămâne alternativa. În sistemul de raportare, plata Booking este dovadă de creanță; încasarea devine confirmată numai prin extrasul bancar.
- **WhatsApp Business Platform:** integrarea oficială se face prin Cloud API, Meta app, WhatsApp Business Account, număr de telefon autorizat și webhook public HTTPS. Endpointul trebuie să valideze handshake-ul webhook și semnătura cererilor, să verifice numărul expeditorului autorizat și să nu scrie payloaduri sau date financiare în loguri. Mesajele sunt tarifate conform ratei Meta aplicabile tipului și destinației; cont și număr nu au fost configurate, iar niciun mesaj nu a fost trimis.
- **Bănci:** fiecare bancă poate exporta câmpuri, semne, monede și formate diferite. Versiunea curentă acceptă CSV cu mapare explicită. Nu presupune acces PSD2/Open Banking și nu cere date de autentificare. Suportul pentru un standard XML bancar se poate adăuga după identificarea formatului și versiunii dintr-un extras anonimizat.

## Starea efectivă a funcțiilor

Importul Booking CSV, importul agregatelor Creasoft în SQLite, registrul local, raportarea HTML, idempotenta importurilor și propunerea/confirmarea manuală a reconcilierii sunt testate local cu date sintetice. Nu a fost configurată nicio conexiune API Booking, Creasoft, bancă sau WhatsApp și nu este instalată o aplicație cloud. Importul specializat Booking și maparea generică sunt două căi de import distincte; nu importa același export pe ambele căi în aceeași evidență.

## Referințe oficiale consultate

- [Booking.com Connectivity APIs — overview and partner onboarding](https://developers.booking.com/connectivity/docs)
- [Booking.com authentication](https://developers.booking.com/connectivity/docs/authentication)
- [Booking.com endpoint/connection access](https://developers.booking.com/connectivity/docs/managing-endpoint-access)
- [Booking.com token-based authentication](https://developers.booking.com/connectivity/docs/token-based-authentication)
- [Creasoft — documentația rapoartelor financiare vending (PDF)](https://creasoft.ro/sites/default/files/2019-10/Modificare%20SITE%20ver%205.pdf)
- [Meta — WhatsApp Business Platform pricing](https://business.whatsapp.com/products/platform-pricing)
- [Meta-hosted WhatsApp Node.js SDK — webhook verification](https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/webhooks/start/)
- [Railway Hobby plan](https://docs.railway.com/pricing/plans), [Render pricing](https://render.com/pricing), [Cloudflare Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/)

Aceste referințe sunt context pentru limitele tehnice; nu confirmă că un API sau un export anume este activ pentru contul utilizatorului.
