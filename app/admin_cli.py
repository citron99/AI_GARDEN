import argparse

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import User


def set_admin(email: str, enabled: bool) -> bool:
    normalized = email.strip().lower()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(func.lower(User.email) == normalized))
        if not user:
            return False
        user.is_admin = enabled
        db.commit()
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Grant or revoke AI Garden administrator role")
    parser.add_argument("action", choices=("grant", "revoke"))
    parser.add_argument("email")
    args = parser.parse_args()
    if not set_admin(args.email, args.action == "grant"):
        print("User not found")
        return 1
    verb = "granted" if args.action == "grant" else "revoked"
    print(f"Administrator role {verb} for {args.email.strip().lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())