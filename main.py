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


def verifica_sistemul():
    pregateste_directoare()

    print("=" * 45)
    print("ASISTENT FINANCIAR")
    print("=" * 45)
    print("Sistemul a pornit cu succes.")
    print(f"Data verificarii: {datetime.now():%d-%m-%Y %H:%M}")
    print()

    print("Surse financiare:")
    print("- Creasoft: raport CSV")
    print("- Booking.com: raport CSV")
    print("- Banca PFA: extras bancar")
    print()

    print("Directoare pregatite:")
    print(f"- {DIRECTOR_DATE}")
    print(f"- {DIRECTOR_RAPOARTE}")
    print()

    print("Urmatorul pas: importul si verificarea datelor.")


if __name__ == "__main__":
    verifica_sistemul()
