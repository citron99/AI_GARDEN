import json
from collections.abc import Sequence

from sqlalchemy import Float
from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType):
    """Portable SQLAlchemy binding for PostgreSQL pgvector's vector type."""

    cache_ok = True

    class comparator_factory(UserDefinedType.Comparator):
        def cosine_distance(self, other):
            return self.expr.op("<=>", return_type=Float())(other)

    def __init__(self, dimensions: int):
        self.dimensions = dimensions

    def get_col_spec(self, **_kw) -> str:
        return f"VECTOR({self.dimensions})"

    def bind_processor(self, _dialect):
        def process(value):
            if value is None or isinstance(value, str):
                return value
            if not isinstance(value, Sequence) or len(value) != self.dimensions:
                raise ValueError(f"Ожидался вектор размерности {self.dimensions}")
            return "[" + ",".join(format(float(item), ".9g") for item in value) + "]"

        return process

    def result_processor(self, _dialect, _coltype):
        def process(value):
            if value is None or isinstance(value, list):
                return value
            if isinstance(value, (bytes, bytearray)):
                value = value.decode("utf-8")
            return [float(item) for item in json.loads(value)]

        return process