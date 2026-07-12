from kilnworks.costmeter import CostEvent, PgCostLedger


class LedgerCostRecorder:
    """Bridges the core CostRecorder port to the standalone cost ledger."""

    def __init__(self, ledger: PgCostLedger):
        self._ledger = ledger

    def record_cost(
        self,
        kind: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        context: str,
        user_id: str | None = None,
    ) -> None:
        self._ledger.record(
            CostEvent(kind=kind, model=model, input_tokens=input_tokens,
                      output_tokens=output_tokens, context=context, user_id=user_id)
        )
