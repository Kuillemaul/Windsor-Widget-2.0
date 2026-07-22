from __future__ import annotations

from types import SimpleNamespace

from windsor_widget.imports.transaction_promotion import _rows


class _BusyAwareResult:
    def __init__(self, session: "_BusyAwareSession", rows: list[tuple[object, ...]]) -> None:
        self.session = session
        self.rows = rows

    def all(self) -> list[tuple[object, ...]]:
        self.session.busy = False
        return list(self.rows)

    def __iter__(self):
        self.session.busy = True
        try:
            yield from self.rows
        finally:
            self.session.busy = False


class _BusyAwareSession:
    def __init__(self) -> None:
        self.busy = False
        self.pages = [
            [(11, 1, "a", '{"values": {}}'), (12, 2, "b", '{"values": {}}')],
            [],
        ]

    def execute(self, _statement):
        return _BusyAwareResult(self, self.pages.pop(0))

    def simulate_write(self) -> None:
        if self.busy:
            raise RuntimeError("connection is busy with results for another command")


def test_staged_rows_close_each_select_before_callers_can_write() -> None:
    session = _BusyAwareSession()
    iterator = _rows(  # type: ignore[arg-type]
        session,
        SimpleNamespace(import_batch_id="batch"),
    )

    first = next(iterator)
    session.simulate_write()
    remaining = list(iterator)

    assert first.import_row_id == 11
    assert [row.import_row_id for row in remaining] == [12]
    assert session.busy is False
