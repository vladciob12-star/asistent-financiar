# Starea integrărilor și cerințe

Evaluare din 16 octombrie 2026. Documentația cloud completă și comparația costurilor: docs/cloud-readiness.md.

## Funcții active în cod

- Creasoft: import manual CSV cu agregate pe automat; perioada lipsă nu este dedusă, iar cash + non-cash nu sunt tratate ca numerar.
- Booking.com: import manual CSV localizat; payout-urile sunt observații de creanță până la potrivirea și confirmarea manuală.
- Gmail → Booking: importer automat opțional, dezactivat implicit; necesită autorizare OAuth read-only configurată separat.
- Banca: import exclusiv manual BT Go MT940 (moneda este citită din fișier) sau CSV/XLSX cu mapare explicită. Nu se cer date de autentificare bancară și nu există conexiune PSD2/Open Banking.
- SQLite: registru local persistent în calea configurată. Pentru cloud calea trebuie montată pe volumul persistent.
- Cloud: imagine Docker, profil Render, autentificare cloud, health check și utilitar de backup criptat sunt pregătite, dar nu au fost deployate sau activate.

## Cloud și protecția datelor

- În modul local, serverul ascultă doar pe loopback și poate fi folosit fără login suplimentar.
- Pentru bind public, aplicația refuză pornirea fără hash PBKDF2 și salt de parolă, URL public HTTPS și cookie securizat. După login, cookie-ul este HttpOnly, SameSite=Strict, expiră după 12 ore și sesiunea expiră la repornirea procesului.
- Fișierele CSV/XLSX/MT940 sunt procesate temporar; registrul păstrează câmpurile normalizate și hash-ul fișierului. Referințele MT940 sunt păstrate doar ca hash; soldul extrasului nu devine venit. Logurile HTTP sunt dezactivate.
- Baza SQLite nu este criptată de aplicație. În profilul Render, discul providerului este criptat în repaus și snapshot-urile sunt criptate. În alți furnizori, trebuie configurată și verificată separat criptarea volumului.
- Utilitarul scripts/backup_database.py creează backup SQLite consistent, apoi îl criptează AES-256-GCM. Cheia trebuie păstrată separat; nu se copiază în Git și nu există upload/scheduler automat.

## Integrare WhatsApp Business oficială

Codul pentru Meta WhatsApp Business Platform Cloud API este implementat, dar integrarea rămâne **neconectată și dezactivată** până când proprietarul completează secretele și setează `WHATSAPP_ENABLE_MESSAGING=true`. Nu folosește BSP.

- `GET /webhook/whatsapp` verifică `hub.verify_token` și răspunde cu challenge-ul numai când tokenul corespunde.
- `POST /webhook/whatsapp` validează HMAC-SHA256 `X-Hub-Signature-256` pe bytes originali cu Meta App Secret; limitează corpul la 1 MB și acceptă numai evenimente WhatsApp Business Account.
- Sunt procesate numai mesaje text de la numerele din `WHATSAPP_ALLOWED_SENDERS`. Mesajele media sunt ignorate, deci extrasele bancare nu pot fi importate prin WhatsApp.
- Se salvează doar ID-urile mesajelor pentru deduplicarea retrimiterilor Meta și se șterg după 30 zile; nu se salvează textul sau numărul expeditorului. Logurile HTTP nu includ payloadul.
- Răspunsurile sunt deterministe și agregate din SQLite: venituri, cheltuieli, profit estimat și sold introdus manual; nu sunt expuse tranzacții individuale și nu se face conversie monetară.
- Raportul PDF poate fi trimis numai la comanda explicită `/raport AAAA-LL` de la expeditorul autorizat. `WHATSAPP_ALLOW_REPORTS` este `false` implicit, iar comanda necesită mesaj primit în ultimele 24 de ore. Nu există scheduler pentru rapoarte și nu sunt implementate mesaje-template/out-of-window.

Activarea necesită Meta Business Portfolio, aplicație Meta cu produs WhatsApp, WABA, număr business eligibil/înregistrat, webhook public HTTPS și abonarea aplicației la `messages`, token system-user cu `whatsapp_business_messaging`, App Secret, Verify Token și allowlist de numere. Proprietarul trebuie să verifice prețul curent Meta și regulile de consimțământ înainte de a trimite mesaje; nu a fost activată nicio conexiune și codul nu pretinde că funcționează cu Meta până la această configurare.

## Telegram Bot API

Telegram este implementat ca integrare separată; nu înlocuiește și nu modifică webhook-ul WhatsApp. Starea implicită este dezactivată. Nu se face polling `getUpdates`; mesajele intră numai prin POST la `/webhook/telegram`.

Configurarea Railway folosește aceste variabile secrete:

- `TELEGRAM_BOT_TOKEN`: tokenul creat de proprietar în conversația oficială cu @BotFather.
- `TELEGRAM_WEBHOOK_SECRET`: valoare aleatorie proprie, 32–128 caractere `[A-Za-z0-9_-]`, trimisă de Telegram în antetul webhook-ului.
- `TELEGRAM_ALLOWED_USER_IDS`: lista separată prin virgulă cu ID-ul numeric Telegram al proprietarului. Username-urile nu sunt folosite la autorizare.
- `TELEGRAM_ENABLE_MESSAGING=true`: activează mesajele după ce celelalte valori sunt validate.
- `TELEGRAM_ALLOW_REPORTS=true`: permite numai PDF-uri cerute explicit prin chat; implicit este dezactivat.
- `TELEGRAM_CONFIGURE_WEBHOOK_ON_START=true`: cere aplicației să înregistreze callback-ul HTTPS bazat pe `APP_PUBLIC_URL` la fiecare pornire/redeploy. Fără HTTPS valid și secrete complete, callback-ul nu se înregistrează.

Handlerul validează secretul webhook-ului cu comparație constant-time, acceptă doar chat privat în care `chat.id` este egal cu `from.id`, aplică allowlist-ul numeric și deduplică `update_id` în tabelul SQLite. Nu persistă textul, numele sau ID-ul de chat și nu loghează request-urile. Mesajele neautorizate primesc HTTP 200 fără răspuns financiar. Mesajele acceptate folosesc aceleași agregate și PDF-uri existente; nu importă fișiere și nu citește extrase bancare automat. Vânzările Creasoft fără perioadă sunt prezentate ca atare, niciodată ca sold fizic al aparatului. Plățile Booking neconfirmate sunt separate de fondurile disponibile. Răspunsurile financiare arată data ultimei importări/salvări de sold în UTC; întrebările despre ERA și Crișul identifică aparatele.


### Documente Telegram

Webhook-ul se abonează la `message` și `callback_query`. Fișierele sunt descărcate numai după verificarea ID-ului numeric autorizat și a chatului privat. Limita locală este 10 MB și 5.000 de rânduri. Se acceptă momentan Booking CSV, Creasoft CSV, MT940 și CSV/XLSX bancar care corespunde exact mapării explicite `DATA`, `DESCRIERE`, `SUMA` în RON. CSV-urile sunt validate împotriva parser-elor Booking și Creasoft înainte de maparea bancară; formatele necunoscute sunt respinse.

Botul prezintă un rezumat și butoanele Confirmă / Anulează / Vezi detalii. Nicio înregistrare nu ajunge în registru înainte de confirmarea proprietarului. Datele parse-uite țin locul fișierului original într-un draft temporar din memoria procesului, cu expirare după 30 de minute; fișierele originale rămân doar într-un director temporar și sunt șterse la terminarea analizei. La restart, drafturile neconfirmate se pierd și documentul trebuie retrimis. Hash-ul documentului împiedică reimportul acelorași octeți; payout-urile Booking rămân neconfirmate până la reconcilierea cu extrasul manual.

PDF-urile, fotografiile/bonurile, XLSX Creasoft/Booking și facturile nu sunt încă procesate: aplicația nu are un parser PDF/OCR local validat. Nu se folosesc servicii AI externe. Soldul bancar nu se deduce din rulaj; tranzacțiile bancare importate rămân neclasificate; vânzările Creasoft cash+non-cash nu stabilesc numerarul din aparate.

Botul și conversația cu @BotFather trebuie create de proprietar; aplicația nu poate accesa identitatea sau tokenul Telegram fără acea configurare. Setarea webhook-ului de pornire și trimiterea mesajelor necesită conexiune la Telegram Bot API și pot consuma traficul inclus al serviciului Railway, fără un abonament Telegram separat. Nu este considerată conectată până când Telegram confirmă setWebhook și un mesaj real de la ID-ul autorizat primește răspuns.

## Creasoft

Pagina oficială Creasoft descrie un API gratuit pentru integrarea payroll/ERP, cu sincronizare de angajați, evenimente și sumarizări, plus import/export Excel. Menționează separat telemetrie pentru vending, dar nu publică un contract API de rapoarte financiare vending. API-ul HR nu se presupune automat compatibil cu rapoartele vending.

Pentru o automatizare read-only trebuie cerute Creasoft: endpoint și schema contului concret, permisiuni, intervale, limitări, cost/contract și definiția totalurilor cash/non-cash. Până la confirmarea lor, importul CSV existent este singura cale disponibilă. Exportul curent are agregate cash + non-cash fără perioadă sigură, deci nu poate calcula evoluția zilnică sau numerarul fizic. Nu există scraping sau sincronizare automată activă.

## Booking.com

Connectivity API este destinat partenerilor Connectivity și proprietăților conectate. E nevoie de aprobare/onboarding, machine account și token, legarea proprietății și activarea endpointurilor necesare. Credential-based auth a fost retrasă la 31 decembrie 2025; integrarea nouă folosește token-uri cu durată limitată. Raportul CSV primit prin e-mail continuă să fie importat manual; e-mailul și contul Booking nu sunt conectate.

Payments API oferă payout/reconciliere pe rezervare doar proprietăților care folosesc Payments by Booking.com și au implementat Reservations API. Reconciliation API poate genera rapoarte payout. Aceste valori pot fi importate automat după autorizare, dar nu dovedesc că banii au intrat în cont: reconcilierea cu banca rămâne dependentă de extrasul importat manual.

### Importul rapoartelor din Gmail

Codul `gmail_booking.py` poate verifica inboxul la 15 minute, dar este **dezactivat implicit** și nu este conectat la niciun cont. Când este configurat, face doar cereri Gmail API read-only pentru căutarea rapoartelor: caută în ultimele 90 de zile, examinează cel mult 1.000 de mesaje per ciclu, verifică domeniul expeditorului (`booking.com` sau subdomeniu) și încearcă numai atașamente `.csv`. CSV-ul trebuie să treacă validatorul Booking folosit și la importul manual.

Importul salvează doar câmpurile financiare normalizate, hash-ul atașamentului și hash-ul referințelor Booking. Nu păstrează corpul e-mailului, subiectul, numele atașamentului sau bytes CSV. Mesajele sunt marcate local ca procesate pentru a evita reimportul; plățile Booking rămân creanțe neconfirmate până la reconcilierea manuală cu extrasul.

Activarea cere configurarea secretelor `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET` și `GMAIL_REFRESH_TOKEN`, apoi setarea explicită `GMAIL_BOOKING_AUTO_IMPORT=true`. Clientul OAuth trebuie să ceară numai `https://www.googleapis.com/auth/gmail.readonly`; codul nu trimite, șterge, arhivează sau etichetează mesaje. Înainte de activare sunt necesare un proiect OAuth Google și autorizarea proprietarului. Gmail clasifică `gmail.readonly` drept scope restricționat; pentru acces server-side pot fi necesare verificarea aplicației și evaluarea de securitate Google. Integrarea nu este considerată funcțională până când autorizarea OAuth și importul unui raport de test fără date reale sunt confirmate.

## Separarea automatizării și aprobărilor

### Se poate automatiza fără acces la servicii terțe

- Inițializarea bazei la pornire, health check și persistența SQLite pe volumul configurat.
- Deduplicarea fișierelor importate, totalurile lunare, PDF-ul și propunerile de reconciliere.
- Crearea sau restaurarea unei copii criptate atunci când proprietarul rulează utilitarul explicit.

### Poate deveni automat după aprobare și configurare

- Polling read-only Gmail pentru atașamente Booking CSV, după configurarea și verificarea OAuth Google.
- Polling read-only pentru rapoarte Creasoft după accesul oficial și testarea unei scheme anonimizate.
- Webhook-uri WhatsApp după configurarea contului Meta, HTTPS, verificarea semnăturii și lista expeditorilor.
- Programarea și transferul backup-urilor după alegerea destinației, retenției și custodei cheii.

### Necesită aprobarea sau acțiunea proprietarului

- Crearea unui serviciu cloud plătit, alegerea bugetului, domeniului și regiunii.
- Introducerea secretelelor cloud și configurarea backup-urilor externe.
- Autorizarea conturilor Creasoft, Booking sau Meta și alegerea datelor care pot fi preluate.
- Crearea/configurarea proiectului OAuth Google, verificarea scope-ului restricționat și introducerea secretelor OAuth în Railway.
- Orice mesaj WhatsApp outbound și orice cost Meta/BSP/cloud.
- Clasificarea contabilă și confirmarea potrivirii dintre payout Booking și extras bancar.
- Importul fiecărui extras bancar: rămâne exclusiv manual; nu se automatizează.

## Referințe oficiale

- Render: [prețuri](https://render.com/pricing), [discuri persistente](https://render.com/docs/disks), [plan gratuit](https://render.com/docs/free).
- Fly.io: [prețuri actualizate 1 octombrie 2026](https://fly.io/pricing-update/).
- Hetzner: [prețuri cloud după ajustarea din 15 iunie 2026](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/).
- Meta: [WhatsApp Business Platform pricing](https://whatsappbusiness.com/products/platform-pricing/), [Cloud API getting started](https://developers.facebook.com/docs/whatsapp/cloud-api/get-started).
- Creasoft: [API și export/import](https://www.creasoft.ro/ro/control-access-made-in-romania).
- Google: [Gmail API OAuth scopes](https://developers.google.com/workspace/gmail/api/auth/scopes), [Gmail API push notifications](https://developers.google.com/workspace/gmail/api/guides/push), [OAuth 2.0 refresh token policies](https://developers.google.com/identity/protocols/oauth2/).
- Booking.com: [Connectivity API](https://developers.booking.com/connectivity/docs), [autentificare](https://developers.booking.com/connectivity/docs/authentication), [acces endpoint](https://developers.booking.com/connectivity/docs/managing-endpoint-access), [Payments API](https://developers.booking.com/connectivity/docs/payments-api/understanding-the-payments-api), [Reconciliation API](https://developers.booking.com/connectivity/docs/openapispecs/reconciliation-api/reconciliation-api-specification).


