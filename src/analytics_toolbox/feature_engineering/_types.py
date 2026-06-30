from dataclasses import dataclass


@dataclass(frozen=True)
class Agg:
    name: str
    expr: str


@dataclass(frozen=True)
class Guardrails:
    max_fanout_rows: int | None = 50_000_000
    on_fanout_exceed: str = "raise"
    require_unique_spine: bool = True

    def __post_init__(self) -> None:
        if self.on_fanout_exceed not in ("raise", "warn"):
            raise ValueError(
                f"on_fanout_exceed must be 'raise' or 'warn', got {self.on_fanout_exceed!r}"
            )
