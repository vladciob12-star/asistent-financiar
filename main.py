import csv
from pathlib import Path
from datetime import datetime

DIRECTOR_RAPOARTE = Path("rapoarte")
FISIER_TEST = Path("rapoarte_test.csv")


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
    else:
        valoare = valoare.replace(",", ".")

    try:
        return float(valoare)
    except ValueError:
        return 0.0


def verifica_raport_test():
    if not FISIER_TEST.exists():
        print("EROARE: Nu gasesc fisierul rapoarte_test.csv.")
        return

    inregistrari = citeste_csv(FISIER_TEST)

    print("=" * 45)
    print("ASISTENT FINANCIAR - TEST")
    print("=" * 45)
    print(f"Data verificarii: {datetime.now():%d-%m-%Y %H:%M}")
    print(f"Numar de inregistrari: {len(inregistrari)}")

    total = 0.0

    for rand in inregistrari:
        sursa = rand.get("sursa", "Necunoscuta")
        data = rand.get("data", "")
        descriere = rand.get("descriere", "")
        suma = transforma_suma(rand.get("suma", "0"))

        total += suma

        print()
        print(f"Sursa: {sursa}")
        print(f"Data: {data}")
        print(f"Descriere: {descriere}")
        print(f"Suma: {suma:.2f} lei")

    print()
    print("-" * 45)
    print(f"TOTAL DE TEST: {total:.2f} lei")
    print("-" * 45)
    print("Test terminat.")


if __name__ == "__main__":
    verifica_raport_test()
