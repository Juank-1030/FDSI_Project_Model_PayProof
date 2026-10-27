import hashlib

from .database import now_iso

HASH_ALGORITHM = "sha256"

VENDORS_SEED = [
    ("Papeleria El Sol", "Banco Andino", "ES91 2100 1234 5100 1234 5678"),
    ("SoftwareLatam SAS", "Banco Pacifico", "ES66 0182 4000 4200 4012 3456"),
    ("Transportes Andinos", "Banco Andino", "CO88 1234 5678 9001 2345 6789"),
]


def normalize_vendor(name: str, bank: str, account: str) -> str:
    def clean(value: str) -> str:
        return "".join(ch for ch in str(value).upper() if ch.isalnum())

    return "|".join([clean(name), clean(bank), clean(account)])


def vendor_hash(name: str, bank: str, account: str) -> str:
    canon = normalize_vendor(name, bank, account)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def register_vendor(conn, name, bank, account, core_account_uuid=None):
    h = vendor_hash(name, bank, account)
    cur = conn.execute(
        "INSERT INTO vendors (nombre, banco, cuenta, hash, core_account_uuid, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'activo', ?, ?)",
        (name, bank, account, h, core_account_uuid, now_iso(), now_iso()),
    )
    return cur.lastrowid


def get_vendor(conn, vendor_id):
    row = conn.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
    return dict(row) if row else None


def find_vendor_by_name(conn, name):
    row = conn.execute(
        "SELECT * FROM vendors WHERE lower(nombre) = lower(?) AND status = 'activo' ORDER BY id",
        (name,),
    ).fetchone()
    return dict(row) if row else None


def verify_vendor(conn, name, bank, account):
    """Compara el hash de los datos entrantes con el registro del proveedor.

    Devuelve un dict con match, hashes y el id del proveedor registrado.
    """
    vendor = find_vendor_by_name(conn, name)
    incoming_hash = vendor_hash(name, bank, account)
    if vendor is None:
        return {
            "match": False,
            "vendor_id": None,
            "registered": False,
            "incoming_hash": incoming_hash,
            "registered_hash": None,
            "core_account_uuid": None,
        }
    return {
        "match": incoming_hash == vendor["hash"],
        "vendor_id": vendor["id"],
        "registered": True,
        "incoming_hash": incoming_hash,
        "registered_hash": vendor["hash"],
        "core_account_uuid": vendor["core_account_uuid"],
    }


def list_vendors(conn):
    rows = conn.execute("SELECT * FROM vendors ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def update_vendor_account(conn, vendor_id, new_bank, new_account, actor):
    vendor = get_vendor(conn, vendor_id)
    if vendor is None:
        raise ValueError("El proveedor indicado no existe.")
    new_hash = vendor_hash(vendor["nombre"], new_bank, new_account)
    conn.execute(
        "UPDATE vendors SET banco = ?, cuenta = ?, hash = ?, updated_at = ? WHERE id = ?",
        (new_bank, new_account, new_hash, now_iso(), vendor_id),
    )
    return new_hash


def ensure_vendors(conn):
    count = conn.execute("SELECT COUNT(*) AS c FROM vendors").fetchone()["c"]
    if count > 0:
        return
    for nombre, banco, cuenta in VENDORS_SEED:
        register_vendor(conn, nombre, banco, cuenta)
