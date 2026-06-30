from __future__ import annotations

import pandas as pd


def join_features(frames: list[pd.DataFrame], on: list[str]) -> pd.DataFrame:
    """Inner-join feature-set outputs on a shared spine grain.

    Every frame must contain exactly the `on` grain columns plus its own
    feature columns. The only columns allowed to appear in more than one
    frame are the grain columns themselves — any other collision is an error.
    """
    if not frames:
        raise ValueError("frames must be a non-empty list")

    on_set = set(on)

    seen: dict[str, int] = {}
    for i, df in enumerate(frames):
        for col in df.columns:
            if col in on_set:
                continue
            if col in seen:
                raise ValueError(
                    f"Column {col!r} appears in both frame {seen[col]} and frame {i} — "
                    "use distinct namespaces to avoid collisions"
                )
            seen[col] = i

    result = frames[0]
    for df in frames[1:]:
        result = result.merge(df, on=on, how="inner")

    return result
