import csv
import json
from datetime import datetime
from pathlib import Path

DIRECTOR_DATE = Path("date")
DIRECTOR_RAPOARTE = Path("rapoarte")


def pregateste_directoare():
    DIRECTOR_DATE.mkdir(exist_ok=True)
    DIRECTOR_RAPOARTE.mkdir(exist_ok=True)


def citeste_csv(fisier):
    with open(fisier, "r", encoding="utf-8-sig", newline="") as document:
        return list(csv.DictReader(document))


def transforma_suma(valoare):
    if not valoare:
        return 0.0

    valoare = str(valoare).strip()
    valoare = valoare.replace(" lei", "").replace("RON", "").replace(" ", "")

    if "," in valoare and "." in valoare:
        if valoare.rfind(",") > valoare.rfind("."):
            valoare = valoare.replace(".", "").replace(",", ".")
        else:
            valoare = valoare.replace(",", "")
    elif "," in valoare:
        valoare = valoare.replace(",", ".")

    try:
        return float(valoare)
    except ValueError:
        return 0.0


def verifica_sistemul():
    pregateste_directoare()

    print("=" * 45)
    print("ASISTENT FINANCIAR")
    print("=" * 45)
    print(f"Data verificarii: {datetime.now():%d-%m-%Y %H:%M}")
    print()
    print("Surse pregatite:")
    print("- Creasoft")
    print("- Booking.com")
    print("- Extras bancar PFA")
    print()
    print("Functii pregatite:")
    print("- Citire fisiere CSV")
    print("- Transformare sume in numere")
    print("- Pregatire directoare pentru date si rapoarte")
    print()
    print("Urmatorul pas: importul raportului Creasoft.")


if __name__ == "__main__":
    verifica_sistemul()
