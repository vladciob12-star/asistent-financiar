import csv
from datetime import datetime
from pathlib import Path

DIRECTOR_RAPOARTE = Path("rapoarte")


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


def verifica_sistemul():
    DIRECTOR_RAPOARTE.mkdir(exist_ok=True)

    print("=" * 45)
    print("ASISTENT FINANCIAR")
    print("=" * 45)
    print(f"Data verificarii: {datetime.now():%d-%m-%Y %H:%M}")

    fisier_test = Path("rapoarte_test.csv")

    if not fisier_test.exists():
        print("EROARE: Nu gasesc fisierul rapoarte_test.csv.")
        return

    randuri = citeste_csv(fisier_test)
    total = 0.0

    for rand in randuri:
        suma = transforma_suma(rand.get("suma", "0"))
        total += suma
        print(
            f"Sursa: {rand.get('sursa', 'Necunoscuta')} | "
            f"Data: {rand.get('data', '-')} | "
            f"Descriere: {rand.get('descriere', '-')} | "
            f"Suma: {suma:.2f} lei"
        )

    print("-" * 45)
    print(f"TOTAL INREGISTRARI DE TEST: {total:.2f} lei")
    print("Verificarea fisierului s-a incheiat.")


if __name__ == "__main__":
    verifica_sistemul()
