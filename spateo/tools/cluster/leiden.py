from typing import Literal, Optional, Union

import anndata
import igraph
import leidenalg
import numpy as np
import scipy
from sklearn.neighbors import kneighbors_graph

from ...configuration import SKM


def distance_knn_graph(dist: np.ndarray, num_neighbors: int):
    """Construct a k-nearest neighbor graph from a distance matrix.

    Args:
        dist: Pairwise distance matrix
        num_neighbors: Number of nearest neighbors

    Returns:
        G: K-nearest neighbor graph
    """
    n = dist.shape[0]
    G = igraph.Graph()
    G.add_vertices(n)
    edges = []
    weights = []
    for i in range(n):
        sorted_ind = np.argsort(dist[i, :])
        for j in range(1, 1 + num_neighbors):
            # if i < sorted_ind[j]:
            edges.append((i, sorted_ind[j]))
            weights.append(dist[i, sorted_ind[j]])
            # weights.append(1.)
    G.add_edges(edges)
    G.es["weight"] = weights
    return G


def embedding_knn_graph(X: np.ndarray, num_neighbors: int):
    """Construct a k-nearest neighbor graph from an arbitrary array, of shape [n_samples, n_features]

    Args:
        X: Embedding matrix
        num_neighbors: Number of nearest neighbors

    Returns:
        G: K-nearest neighbor graph
    """
    adj = kneighbors_graph(X, num_neighbors, include_self=False, mode="distance")
    G = igraph.Graph.Weighted_Adjacency(adj)
    return G


def adj_to_igraph(adj: np.ndarray):
    """Convert an adjacency matrix to an igraph graph."""
    G = igraph.Graph.Adjacency(adj)
    return G


def calculate_leiden_partition(
    adj: Optional[Union[scipy.sparse.spmatrix, np.ndarray]] = None,
    input_mat: Optional[np.ndarray] = None,
    num_neighbors: int = 10,
    graph_type: Literal["distance", "embedding"] = "distance",
    resolution: float = 1.0,
    n_iterations: int = -1,
) -> np.ndarray:
    """Performs Leiden clustering on a given dataset.

    Args:
        adj: Optional precomputed adjacency matrix
        input_mat: Optional, will be used only if 'adj' is not given. The input data, will be interepreted as either a
            distance matrix (if :param `graph_type` is "distance" or an embedding matrix (if :param `graph_type` is
            "embedding")
        num_neighbors: Only used if 'adj' is not given- the number of nearest neighbors for constructing the graph
        graph_type: Only used if 'adj' is not given- specifies the input type, either 'distance' or 'embedding'
        resolution: The resolution parameter for the Leiden algorithm
        n_iterations: The number of iterations for the Leiden algorithm (-1 for unlimited iterations)

    Returns:
        clusters: Array containing cluster assignments
    """
    from ...logging import logger_manager as lm

    logger = lm.get_main_logger()
    if adj is None and input_mat is None:
        raise ValueError("Either `adj` or `input_mat` must be specified")

    logger.info("using adj_matrix from arg for clustering...")

    if adj is not None:
        if scipy.sparse.issparse(adj):
            pass
        else:
            adj = scipy.sparse.csr_matrix(adj)
        sources, targets = adj.nonzero()
        weights = adj[sources, targets]
        if isinstance(weights, np.matrix):
            weights = weights.A1
        G = igraph.Graph(directed=None)
        G.add_vertices(adj.shape[0])  # this adds adjacency.shape[0] vertices
        G.add_edges(list(zip(sources, targets)))
        try:
            G.es["weight"] = weights
        except KeyError:
            pass
        if G.vcount() != adj.shape[0]:
            print(
                f"The constructed graph has only {G.vcount()} nodes. "
                "Your adjacency matrix contained redundant nodes."
            )
    else:
        if graph_type == "distance":
            G = distance_knn_graph(input_mat, num_neighbors)
        elif graph_type == "embedding":
            G = embedding_knn_graph(input_mat, num_neighbors)
    logger.info("Converting graph_sparse_matrix to igraph object", indent_level=2)
    partition_kwargs = {"resolution_parameter": resolution, "seed": 888, "n_iterations": n_iterations}
    partition = leidenalg.find_partition(G, leidenalg.RBConfigurationVertexPartition, **partition_kwargs)
    clusters = np.array(partition.membership, dtype=int)
    logger.finish_progress(progress_name="Community clustering with %s" % ("leiden"))
    return clusters


def calculate_louvain_partition(
    adj: Optional[Union[scipy.sparse.spmatrix, np.ndarray]] = None,
    input_mat: Optional[np.ndarray] = None,
    num_neighbors: int = 10,
    graph_type: Literal["distance", "embedding"] = "distance",
    resolution: float = 1.0,
    n_iterations: int = -1,
) -> np.ndarray:
    """Performs Louvain clustering on a given dataset.

    Args:
        adj: Optional precomputed adjacency matrix
        input_mat: Optional, will be used only if 'adj' is not given. The input data, will be interepreted as either a
            distance matrix (if :param `graph_type` is "distance" or an embedding matrix (if :param `graph_type` is
            "embedding")
        num_neighbors: Only used if 'adj' is not given- the number of nearest neighbors for constructing the graph
        graph_type: Only used if 'adj' is not given- specifies the input type, either 'distance' or 'embedding'
        resolution: The resolution parameter for the Louvain algorithm
        n_iterations: The number of iterations for the Louvain algorithm (-1 for unlimited iterations)

    Returns:
        clusters: Array containing cluster assignments
    """
    import louvain

    from ...logging import logger_manager as lm

    logger = lm.get_main_logger()
    if adj is None and input_mat is None:
        raise ValueError("Either `adj` or `input_mat` must be specified")

    logger.info("using adj_matrix from arg for clustering...")

    if adj is not None:
        if scipy.sparse.issparse(adj):
            pass
        else:
            adj = scipy.sparse.csr_matrix(adj)
        sources, targets = adj.nonzero()
        weights = adj[sources, targets]
        if isinstance(weights, np.matrix):
            weights = weights.A1
        G = igraph.Graph(directed=None)
        G.add_vertices(adj.shape[0])  # this adds adjacency.shape[0] vertices
        G.add_edges(list(zip(sources, targets)))
        try:
            G.es["weight"] = weights
        except KeyError:
            pass
        if G.vcount() != adj.shape[0]:
            print(
                f"The constructed graph has only {G.vcount()} nodes. "
                "Your adjacency matrix contained redundant nodes."
            )
    else:
        if graph_type == "distance":
            G = distance_knn_graph(input_mat, num_neighbors)
        elif graph_type == "embedding":
            G = embedding_knn_graph(input_mat, num_neighbors)
    logger.info("Converting graph_sparse_matrix to igraph object", indent_level=2)
    partition_kwargs = {"resolution_parameter": resolution, "seed": 42, "weights": G.es["weight"]}
    partition = louvain.find_partition(G, louvain.RBConfigurationVertexPartition, **partition_kwargs)
    clusters = np.array(partition.membership, dtype=int)
    logger.finish_progress(progress_name="Community clustering with %s" % ("louvain"))
    return clusters
