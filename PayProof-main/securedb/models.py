import hashlib
import json
from typing import Optional, Union

from pydantic import BaseModel, Field


class OperationProposal(BaseModel):
    operation_type: str = Field(
        description="Tipo de operación: select, update, delete, notify, drop_table, drop_database, modify_audit, alter_credentials o pay"
    )
    target: str = Field(
        description="Tabla o recurso destino: empleados, departamentos, notificaciones, auditoria, sistema o pagos"
    )
    intent: str = Field(description="Resumen breve de la intención del usuario")
    records_affected: int = Field(
        default=1, description="Número estimado de registros afectados (1 si es uno solo)"
    )
    involves_financial: bool = Field(
        default=False,
        description="True si toca salarios, presupuestos o datos financieros",
    )
    involves_personal: bool = Field(
        default=False,
        description="True si toca datos personales sensibles (documentos, direcciones, teléfonos)",
    )
    reversible: bool = Field(
        default=True,
        description="False si la operación no tiene reversión sencilla (eliminaciones o sobreescrituras)",
    )
    recipient_scope: str = Field(
        default="individual",
        description="Para notificaciones: individual, small_group o large_group",
    )
    filters: dict = Field(
        default_factory=dict,
        description="Filtros para localizar registros: id, nombre, apellido, cargo, email o departamento",
    )
    changes: dict = Field(
        default_factory=dict,
        description="Columnas a modificar con su nuevo valor: salario, cargo o email",
    )
    message: str = Field(default="", description="Texto del mensaje si es una notificación o motivo del pago")
    vendor_name: Optional[str] = Field(
        default=None, description="Nombre del proveedor beneficiario del pago"
    )
    bank_code: Optional[str] = Field(
        default=None, description="Banco del beneficiario del pago"
    )
    account_number: Optional[str] = Field(
        default=None,
        description="Número de cuenta o IBAN del beneficiario, tal como lo indica el usuario",
    )
    amount: Optional[float] = Field(
        default=None, description="Monto del pago en unidades de la divisa"
    )
    currency: str = Field(default="EUR", description="Código de divisa del pago (EUR, USD, ...)")

    def payload_hash(self) -> str:
        canon = json.dumps(
            self.model_dump(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()


class RiskAssessment(BaseModel):
    level: Union[int, str]
    label: str
    prohibited: bool = False
    requires_approval: bool = False
    min_authority: Optional[str] = None
    requires_confirmation: bool = False
    reasons: list = Field(default_factory=list)
