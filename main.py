from datetime import datetime


def main():
    print("=" * 45)
    print("ASISTENT FINANCIAR")
    print("=" * 45)
    print("Sistemul a pornit cu succes.")
    print(f"Data verificarii: {datetime.now():%d-%m-%Y %H:%M}")
    print()
    print("Surse financiare planificate:")
    print("- Booking.com")
    print("- myPOS")
    print("- Creasoft")
    print("- Extras bancar PFA")
    print()
    print("Urmatorul pas: conectarea surselor de date.")


if __name__ == "__main__":
    main()
