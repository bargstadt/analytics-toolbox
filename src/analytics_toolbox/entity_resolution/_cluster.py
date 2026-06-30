"""NetworkX-based entity cluster construction.

Builds an undirected weighted graph from match pairs, extracts connected
components as entity clusters, computes per-cluster confidence metrics,
and pivots the result to a wide DataFrame with one column per source system.

Nodes: (system_name, record_id) tuples.
Edges: a matched pair with weight = similarity score.
Connected components: each component is one entity cluster.

Output: one row per cluster, one column per system_id key (None if absent),
plus avg_similarity and min_similarity.
"""

from __future__ import annotations

import logging

import pandas as pd

try:
    import networkx as nx
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "networkx is required for entity_resolution. "
        "Install with: pip install analytics-toolbox[entity-resolution]"
    ) from exc

log = logging.getLogger(__name__)


def build_clusters(
    pairs: list[tuple[str, str, str, str, float]],
    systems: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Build entity clusters from match pairs using NetworkX connected components.

    Args:
        pairs: List of (sys_a_name, id_a, sys_b_name, id_b, score) from score_pairs().
        systems: Original systems dict — used only to determine output column names.

    Returns:
        DataFrame with one row per entity cluster. Columns:
        - One column per system_id key in ``systems`` (None if system not in cluster)
        - ``avg_similarity``: mean edge weight for this component
        - ``min_similarity``: minimum edge weight for this component (worst-case link)
    """
    if not pairs:
        return _empty_result(systems)

    g = _build_graph(pairs)
    system_names = list(systems.keys())

    rows: list[dict] = []
    total_collisions = 0
    for component in nx.connected_components(g):
        row, n_collisions = _component_row(g.subgraph(component), system_names)
        rows.append(row)
        total_collisions += n_collisions

    if not rows:
        return _empty_result(systems)

    if total_collisions > 0:
        log.warning(
            "entity_resolution: %d record(s) dropped from cluster output due to "
            "same-system collisions (multiple records from one system merged into "
            "a single cluster). The strongest-linked record was kept for each.",
            total_collisions,
        )

    # Deterministic ordering: descending avg_similarity, then the system-ID tuple.
    rows.sort(
        key=lambda r: (
            -r["avg_similarity"],
            tuple("" if r[name] is None else str(r[name]) for name in system_names),
        )
    )
    return pd.DataFrame(rows)[system_names + ["avg_similarity", "min_similarity"]]


def _build_graph(pairs: list[tuple[str, str, str, str, float]]) -> nx.Graph:
    """Construct the undirected match graph; on a duplicate edge keep the higher score."""
    g = nx.Graph()
    for sys_a, id_a, sys_b, id_b, score in pairs:
        node_a = (sys_a, id_a)
        node_b = (sys_b, id_b)
        if g.has_edge(node_a, node_b):
            # Shouldn't happen given how score_pairs emits pairs, but be safe.
            g[node_a][node_b]["weight"] = max(g[node_a][node_b]["weight"], score)
        else:
            g.add_edge(node_a, node_b, weight=score)
    return g


def _component_row(
    subgraph: nx.Graph, system_names: list[str]
) -> tuple[dict, int]:
    """Turn one connected component into a wide output row plus its collision count.

    The wide format has one slot per system. When transitive merging puts multiple
    records from the SAME system into one cluster, only one can be represented;
    ``_records_by_system`` resolves that deterministically and the surplus records
    are counted (never logged) as collisions.
    """
    edge_weights = [d["weight"] for _, _, d in subgraph.edges(data=True)]
    row: dict = {name: None for name in system_names}
    row["avg_similarity"] = sum(edge_weights) / len(edge_weights)
    row["min_similarity"] = min(edge_weights)

    n_collisions = 0
    for sys_name, candidates in _records_by_system(subgraph, system_names).items():
        row[sys_name] = candidates[0]
        n_collisions += len(candidates) - 1
    return row, n_collisions


def _records_by_system(
    subgraph: nx.Graph, system_names: list[str]
) -> dict[str, list[str]]:
    """Group a component's nodes by system, ordered by link strength then record_id.

    For each system, returns its record IDs sorted so that the strongest-linked
    record (highest incident edge weight) comes first; ties broken by record_id
    for full determinism.
    """
    grouped: dict[str, list[tuple[float, str]]] = {}
    for sys_name, record_id in subgraph.nodes():
        if sys_name not in system_names:
            continue
        strength = max(
            (d["weight"] for _, _, d in subgraph.edges((sys_name, record_id), data=True)),
            default=0.0,
        )
        grouped.setdefault(sys_name, []).append((strength, record_id))

    ordered: dict[str, list[str]] = {}
    for sys_name, entries in grouped.items():
        entries.sort(key=lambda e: (-e[0], str(e[1])))
        ordered[sys_name] = [record_id for _, record_id in entries]
    return ordered


def _empty_result(systems: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return an empty DataFrame with the correct schema."""
    cols = list(systems.keys()) + ["avg_similarity", "min_similarity"]
    return pd.DataFrame(columns=cols)
