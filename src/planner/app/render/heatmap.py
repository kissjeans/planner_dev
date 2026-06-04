"""Load heatmap PNG: person × day, coloured by hours used (spec 7.4)."""

from __future__ import annotations

import io
from datetime import date

import matplotlib

matplotlib.use("Agg")  # headless: no display, render straight to bytes
import matplotlib.pyplot as plt  # noqa: E402


def render_heatmap(
    labels: list[str], days: list[date], matrix: list[list[int]], capacity: int = 8
) -> bytes:
    """Render an N-people × M-days load matrix to PNG bytes.

    Cells above ``capacity`` read as overload (the colour scale is centred on
    ``capacity`` so red == over budget).
    """
    fig, ax = plt.subplots(figsize=(max(6, len(days) * 0.6), max(2, len(labels) * 0.5)))
    img = ax.imshow(matrix, cmap="RdYlGn_r", vmin=0, vmax=capacity * 2, aspect="auto")

    ax.set_xticks(range(len(days)))
    ax.set_xticklabels([d.strftime("%d.%m") for d in days], rotation=45, ha="right")
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels)
    for i, row in enumerate(matrix):
        for j, val in enumerate(row):
            ax.text(j, i, str(val), ha="center", va="center", fontsize=8)
    fig.colorbar(img, ax=ax, label="часы")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return buf.getvalue()
