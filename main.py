import csv
from pathlib import Path
from collections import defaultdict
from datetime import datetime

# Folderul în care vor fi puse rapoartele Creasoft
DIRECTOR = Path("rapoarte/creasoft")

COLOANA_VENDOMAT = "Vendomat"
COLOANA_CANTITATE = "Cantitate"
COLOANA_INCASARI = "Incasari totale (cash + non-cash - rest)"


def transforma_numar(valoare):
    """Transforma numerele din formatul romanesc in numere Python."""
    valoare = str(valoare or "0").strip()
    valoare = valoare.replace("lei", "").replace("RON", "")
    valoare = valoare.replace(" ", "")

    if "," in valoare:
        valoare = valoare.replace(".", "").replace(",", ".")

    try:
        return float(valoare)
    except ValueError:
        raise ValueError(f"Numar invalid: {valoare}")


def citeste_raport(fisier):
    """Citeste un raport CSV exportat din Creasoft."""
    with open(fisier, "r", encoding="utf-8-sig", newline="") as document:
        cititor = csv.DictReader(document, delimiter=";")

        if not cititor.fieldnames:
            raise ValueError("Fisierul CSV nu are antet.")

        coloane = {
            coloana.strip().lstrip("\ufeff")
            for coloana in cititor.fieldnames
        }

        necesare = {
            COLOANA_VENDOMAT,
            COLOANA_CANTITATE,
            COLOANA_INCASARI,
        }

        lipsa = necesare - coloane

        if lipsa:
            raise ValueError(
                "Lipsesc coloanele: " + ", ".join(sorted(lipsa))
            )

        rezultate = defaultdict(lambda: {
            "cantitate": 0,
            "incasari": 0.0,
        })

        for rand in cititor:
            vendomat = rand[COLOANA_VENDOMAT].strip()

            if not vendomat:
                continue

            cantitate = transforma_numar(
                rand[COLOANA_CANTITATE]
            )

            incasari = transforma_numar(
                rand[COLOANA_INCASARI]
            )

            rezultate[vendomat]["cantitate"] += int(cantitate)
            rezultate[vendomat]["incasari"] += incasari

        if not rezultate:
            raise ValueError("Raportul nu contine inregistrari.")

        return rezultate


def proceseaza_rapoarte():
    print("=" * 55)
    print("ASISTENT FINANCIAR - RAPOARTE CREASOFT")
    print("=" * 55)
    print("Data procesarii:", datetime.now().strftime("%d-%m-%Y %H:%M"))
    print()

    if not DIRECTOR.exists():
        print("Folderul rapoarte/creasoft nu exista.")
        print("Creeaza folderul si pune in el rapoartele CSV.")
        return

    fisiere = sorted(DIRECTOR.glob("*.csv"))

    if not fisiere:
        print("Nu exista rapoarte CSV in folder.")
        return

    for fisier in fisiere:
        print("-" * 55)
        print("Fisier:", fisier.name)

        try:
            rezultate = citeste_raport(fisier)

            total_cantitate = 0
            total_incasari = 0.0

            for vendomat, date in sorted(rezultate.items()):
                print()
                print("Automat:", vendomat)
                print("Produse vandute:", date["cantitate"])
                print(f"Incasari: {date['incasari']:.2f} lei")

                total_cantitate += date["cantitate"]
                total_incasari += date["incasari"]

            print()
            print("TOTAL RAPORT")
            print("Produse vandute:", total_cantitate)
            print(f"Incasari totale: {total_incasari:.2f} lei")

        except (ValueError, KeyError, OSError) as eroare:
            print("EROARE:", eroare)

        print()

    print("=" * 55)
    print("PROCESARE INCHEIATA")
    print("=" * 55)


if _name_ == "_main_":
    proceseaza_rapoarte()
