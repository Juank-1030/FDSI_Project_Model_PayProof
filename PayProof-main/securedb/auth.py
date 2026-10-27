import bcrypt

from .database import get_conn

DEMO_USERS = [
    ("admin", "Admin123!", "administrador"),
    ("supervisor", "Supervisor123!", "supervisor"),
    ("operador", "Operador123!", "operador"),
    ("usuario", "Usuario123!", "usuario"),
]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def ensure_demo_users():
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        if count > 0:
            return
        for username, password, role in DEMO_USERS:
            conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                (username, hash_password(password), role),
            )


def authenticate(username: str, password: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        return {"username": row["username"], "role": row["role"]}
