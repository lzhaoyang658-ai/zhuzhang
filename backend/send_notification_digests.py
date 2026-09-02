from app.database import SessionLocal
from app.services.notification_digest import send_due_notification_digests


def main() -> None:
    with SessionLocal() as db:
        result = send_due_notification_digests(db)
    print(f"notification digests: sent={result['sent']} skipped={result['skipped']} failed={result['failed']}")


if __name__ == "__main__":
    main()
