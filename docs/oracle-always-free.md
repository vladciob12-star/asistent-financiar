# Profil Oracle Cloud Always Free (pregătit, neactivat)

Instantaneu de prețuri și limite verificat la 17 octombrie 2026. Fișierele din `deploy/oracle/` sunt doar configurație; nu s-a creat cont, instanță, domeniu, IP sau backup cloud. Niciun fișier financiar real nu a fost copiat.

## Recomandare și cost estimat

Pentru a încerca 0 lei/lună cu aplicația Python, SQLite persistentă și acces HTTPS, recomand o singură instanță **VM.Standard.E2.1.Micro** Ubuntu/Oracle Linux în regiunea principală. Are 1 GB RAM, resurse CPU mici și documentația spune că rețeaua instanței include un IP public; pentru aplicația cu trafic redus este suficientă configurație de început, dar va fi mai lentă la rapoarte/importuri. Dacă pagina de creare afișează public IPv4 sau alt element cu taxă, oprește-te înainte de creare. A1 Flex oferă mai multă memorie/CPU (până la 2 OCPU/12 GB total Always Free în documentația consultată), dar opțiunea E2 face IP-ul explicit în detaliile shape-ului. Ambele trebuie să rămână în regiunea principală și strict în quota Always Free.

| Opțiune | Cost lunar pentru aplicație mereu pornită | IP public | Stocare și backup | Trafic |
|---|---:|---|---|---|
| **Oracle Always Free** | **0 lei** în cotele eligibile | VM-ul E2.1.Micro include VNIC cu IP public; pentru A1, alocarea IP este configurată de proprietar. Oracle documentează eligibilitatea IP pentru instanță, dar prețul public separat pentru IP nu este exprimat clar în pagina Free Tier; verifică estimatorul/estimarea Console la $0 înainte de creare. DNS propriu poate costa; IP-ul efemer se poate schimba la recreare. | 200 GB Block Volume total în regiunea principală; 5 backup-uri de volum simultan. Configurația oferă backup SQLite AES-256-GCM zilnic local și retenție 14 zile; aceasta nu substituie copii offsite. | 10 TB outbound/lună incluse. |
| Google Cloud Free Tier | 0 lei posibil în limită, dar depășirile sunt facturate | Google afirmă că IP-ul extern pentru e2-micro inclus nu se taxează; VM-ul gratuit disponibil e2-micro este numai în 3 regiuni SUA. | 30 GB-month standard persistent disk; nu include backup automat independent configurat aici. | 1 GB outbound din America de Nord/lună, apoi facturare. Necesită billing account. |
| Railway Free | $0 doar până la $1/lună credit; la 0.5 GB RAM ținut pornit costul memoriei singure este circa **$5/lună**, plus CPU/volum/egress. Serviciul se oprește când creditul se consumă. | Railway service domain inclus; custom domains nu sunt incluse după trial | Volum max 0.5 GB; backup-urile consumă stocare facturată. | Egress $0.05/GB. Include doar $1 utilizare/lună după trial. |

Costurile Oracle/GCP pot deveni nenule dacă se creează resurse în afara Free Tier, regiune greșită, IP/LB suplimentar, stocare/backups peste cotă sau trafic peste cotă. Nu apăsa „Upgrade”, nu adăuga resurse opționale și verifică pagina estimării să arate exact $0 înainte de orice activare. Nu se folosește Load Balancer, NAT Gateway, Vault plătit sau domeniu achiziționat. OCI oferă 20 GB Object Storage gratuit în anumite stări de cont, dar această variantă nu îl configurează automat; o copie externă/în altă regiune cere evaluarea prețului afișat și permisiuni suplimentare.

## Disponibilitate și WhatsApp

Oracle poate recupera instanțe Always Free pe care le consideră idle: în 7 zile CPU p95 sub 20%, rețea sub 20%, iar pentru A1 și memoria sub 20%. Un asistent cu trafic redus poate intra exact în acest caz. Nu există SLA pentru Always Free, iar capacity shortage în regiunea principală poate împiedica crearea/recrearea; la recuperare/repornire IP-ul efemer și serviciul pot fi indisponibile temporar. Astfel, Always Free nu garantează recepția permanentă a webhook-urilor.

Aplicația curentă **nu are endpoint WhatsApp webhook și nu este conectată la WhatsApp Business**. HTTPS și IP public sunt doar condiții de infrastructură; integrarea oficială mai cere Meta Business Portfolio/WABA, număr eligibil, token/secrete, configurarea webhook-ului și endpoint de verificare HTTPS în aplicație. Nu a fost activată sau pretinsă nicio integrare.

## Protecția datelor și backup

- Baza SQLite și exporturile rămân numai pe volumul persistent montat la `/srv/asistent-financiar/data`; accesul public trece prin Caddy HTTPS. Portul aplicației 10000 nu este publicat pe host.
- Înainte de expunere sunt obligatorii URL-ul HTTPS, parola PBKDF2 hash-uită și salt-ul. Păstrează `.env` în afara Git, cu permisiuni restrictive; nu pune parole/chei în imagine sau repository.
- Integrarea Meta se oprește implicit prin `WHATSAPP_ENABLE_MESSAGING=false` și `WHATSAPP_ALLOW_REPORTS=false`. Dacă o configurezi, webhook-ul folosește exact aceeași origine HTTPS pe `/webhook/whatsapp`; nu dezactiva autentificarea dashboardului pentru a-l permite.
- Instalează unitățile systemd din acest director pentru backup criptat zilnic în același volum, cu retenție 14 zile. Cheia AES de 32 bytes (`BACKUP_ENCRYPTION_KEY`) trebuie creată și păstrată separat de server. Copiile locale dispar dacă volumul este pierdut. Recomand să activezi din consola OCI o politică de backup de volum eligibilă și să verifici că rămân în cele cinci backup-uri Always Free; confirmă costul $0 afișat înainte de salvare. Backup-urile automate Oracle sunt snapshot de volum, nu o copie portabilă offsite.
- Activează UFW/security list doar pentru TCP 22 (restrâns la IP-ul tău), 80 și 443; nu expune SQLite, SSH tuturor adreselor sau portul 10000. Aplică actualizări de securitate ale OS și imaginilor și testează restaurarea pe o cale separată înainte de utilizare reală.
- Extrasele bancare rămân exclusiv import manual prin interfața autentificată. Nu există conectare bancară automată.

## Pașii proprietarului pentru activare ulterioară

1. Creează/folosește tenancy Oracle și selectează regiunea principală potrivită. Oracle poate cere număr de telefon și card de verificare; acesta nu este aprobare pentru a activa resurse facturabile. Nu face upgrade la cont plătit.
2. În consola OCI verifică quota Always Free, disponibilitatea A1/E2 Micro, 50 GB boot volume, IP public eligibil și prețul estimat de $0. Dacă consola arată cost pentru vreun element, oprește-te.
3. Creează VM cu imagine marcată Always Free Eligible; configurează VCN publică, internet gateway, adresă IP eligibilă și firewall. Instalează Docker Compose, copiază codul/configurația pe server și `deploy/oracle/.env.example` în `.env` cu permisiuni 600. Copiază `caddy.env.example` în `caddy.env` și configurează același nume DNS în ambele fișiere. Caddy primește doar variabila cu numele de domeniu; secretele aplicației nu îi sunt trimise.
4. Configurează un nume DNS. Un domeniu înregistrat poate costa. Pentru 0 lei folosește numai un subdomeniu dinamic gratuit de la un furnizor ales de tine, dacă termenii lui permit; acesta nu e inclus și nu este configurat aici.
5. Generează parola/hash-ul cu `python web_app.py --hash-password` și cheia backupului local cu `python -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"` pe dispozitivul tău de încredere. Introdu hash-ul și salt-ul în `.env`; păstrează cheia de backup în manager de parole/copiere offline, separat de instanță și repository.
6. Creează pe VM `/etc/asistent-financiar/backup.env` cu `BACKUP_ENCRYPTION_KEY=...`, proprietar root și mod 600. Instalează `asistent-financiar-backup.service` și `.timer` în `/etc/systemd/system`, apoi activează timerul cu `systemctl enable --now asistent-financiar-backup.timer`. Configurează/verifică backupurile de volum în cota gratuită și efectuează o restaurare de probă cu date sintetice. Monitorizează costul lunar și notificările de reclaim/capacity.
7. După decizia ta separată poți configura integrarea WhatsApp deja implementată; disponibilitatea Oracle Free nu este garantată pentru webhook-uri permanente.

### Activarea WhatsApp după lansarea serverului

1. În Meta for Developers creează aplicația Business/WhatsApp și configurează un număr business eligibil, WABA și system-user token cu `whatsapp_business_messaging`. Notează Phone Number ID și Graph API version suportate de aplicația ta.
2. Generează un Verify Token aleator pentru callback și păstrează App Secret/token în manager de parole. Completează pe server numai secretele din `.env`; nu le trimite în conversație și nu le comite.
3. Configurează callback-ul Meta ca `https://<domeniul-tău>/webhook/whatsapp`, același Verify Token, apoi verifică webhook-ul și abonează aplicația la câmpul `messages`.
4. După testul Meta și allowlist cu numărul tău în formatul `from` primit (cifre, fără `+`), setează `WHATSAPP_ENABLE_MESSAGING=true`. Pentru PDF la cerere activează separat `WHATSAPP_ALLOW_REPORTS=true`; nu există programare lunară automată.
5. Scrie `/ajutor`, verifică un răspuns cu date sintetice și apoi `/raport AAAA-LL`. Mesajul inițiator trebuie să fie recent; integrarea refuză să răspundă dacă evenimentul are timestamp în afara celor 24 de ore. În afara ferestrei, mesajele initiate de business cer template aprobat și pot costa; această funcționalitate nu este implementată.
6. API-ul acceptă doar text. Nu trimite extras bancar sau PDF în chat; banca continuă să fie importată manual în aplicație. Verifică rate card-ul Meta pentru România și orice condiții ale contului înainte de a activa răspunsurile.

Configurația de pornire este `deploy/oracle/compose.yaml`, Caddy obține și reînnoiește HTTPS automat pentru numele DNS configurat. Pentru rețele private sau IP stabil, nu adăuga Load Balancer/NAT Gateway, deoarece nu sunt necesare și pot crea costuri.
