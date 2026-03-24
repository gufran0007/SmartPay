from app.models.database import SessionLocal, User
from werkzeug.security import generate_password_hash, check_password_hash

class AuthService:
    def register(self, email, password):
        db = SessionLocal()
        exists = db.query(User).filter_by(email=email).first()
        if exists:
            db.close()
            return False
        user = User(email=email, password=generate_password_hash(password))
        db.add(user)
        db.commit()
        db.close()
        return True

    def login(self, email, password):
        db = SessionLocal()
        user = db.query(User).filter_by(email=email).first()
        db.close()
        if not user or not check_password_hash(user.password, password):
            return False
        return True
