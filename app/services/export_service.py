from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence

from fastapi.responses import StreamingResponse


def build_csv_bytes(*, headers: Sequence[str], rows: Iterable[Sequence[object]]) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(list(headers))
    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
    return output.getvalue().encode("utf-8-sig")


def csv_streaming_response(*, filename: str, headers: Sequence[str], rows: Iterable[Sequence[object]]) -> StreamingResponse:
    content = build_csv_bytes(headers=headers, rows=rows)
    return StreamingResponse(
        io.BytesIO(content),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
