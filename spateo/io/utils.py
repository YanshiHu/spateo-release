"""IO utility functions.
"""
import math
from typing import Optional, Tuple, Union

import cv2
import numpy as np
import pandas as pd
from anndata import AnnData
from scipy.sparse import csr_matrix, issparse, spmatrix
from scipy.spatial import Delaunay
from shapely.geometry import Point, LineString, Polygon, MultiPolygon
from skimage import measure


def bin_indices(coords: np.ndarray, coord_min: float, binsize: int = 50) -> int:
    """Take a DNB coordinate, the mimimum coordinate and the binsize, calculate the index of bins for the current
    coordinate.

    Parameters
    ----------
        coord: `float`
            Current x or y coordinate.
        coord_min: `float`
            Minimal value for the current x or y coordinate on the entire tissue slide measured by the spatial
            transcriptomics.
        binsize: `float`
            Size of the bins to aggregate data.

    Returns
    -------
        num: `int`
            The bin index for the current coordinate.
    """
    num = np.floor((coords - coord_min) / binsize)
    return num.astype(np.uint32)


def centroids(bin_indices: np.ndarray, coord_min: float = 0, binsize: int = 50) -> float:
    """Take a bin index, the mimimum coordinate and the binsize, calculate the centroid of the current bin.

    Parameters
    ----------
        bin_ind: `float`
            The bin index for the current coordinate.
        coord_min: `float`
            Minimal value for the current x or y coordinate on the entire tissue slide measured by the spatial
            transcriptomics.
        binsize: `int`
            Size of the bins to aggregate data.

    Returns
    -------
        num: `int`
            The bin index for the current coordinate.
    """
    coord_centroids = coord_min + bin_indices * binsize + binsize / 2
    return coord_centroids


def get_label_props(
        label_mtx: np.ndarray,
        properties: Tuple[str, ...] = ("label", "area", "bbox", "centroid"),
) -> pd.DataFrame:
    """Measure properties of labeled cell regions.

    Parameters
    ----------
        label_mtx: `numpy.ndarray`
            cell segmentation label matrix
        properties: `tuple`
            used properties

    Returns
    -------
        props: `pandas.DataFrame`
            A dataframe with properties and contours

    """

    def contours(mtx):
        """Get contours of a cell using `cv2.findContours`."""
        # padding and transfer label mtx to binary mtx
        mtx = np.pad(mtx, 1)
        mtx[mtx > 0] = 255
        mtx = mtx.astype(np.uint8)
        # get contours
        contour = cv2.findContours(mtx, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0][0]
        # shift back coordinates
        contour = contour - np.array([1, 1])
        return contour

    def contour_to_geo(contour):
        """Transfer contours to `shapely.geometry`"""
        n = contour.shape[0]
        contour = np.squeeze(contour)
        if n >= 3:
            geo = Polygon(contour)
        elif n == 2:
            geo = LineString(contour)
        else:
            geo = Point(contour)
        return geo

    props = measure.regionprops_table(label_mtx, properties=properties, extra_properties=[contours])
    props = pd.DataFrame(props)
    props["contours"] = props.apply(lambda x: x["contours"] + x[["bbox-0", "bbox-1"]].to_numpy(), axis=1)
    props["contours"] = props["contours"].apply(contour_to_geo)
    return props


def get_bin_props(data: pd.DataFrame, binsize: int) -> pd.DataFrame:
    """Simulate properties of bin regions.

    Parameters
    ----------
        data :
            The index of coordinates.
        binsize :
            The number of spatial bins to aggregate RNAs captured by DNBs in those bins.

    Returns
    -------
        props: `pandas.DataFrame`
            A dataframe with properties and contours

    """

    def create_geo(row):
        x, y = row["x_ind"], row["y_ind"]
        x *= binsize
        y *= binsize
        if binsize > 1:
            geo = Polygon(
                [
                    (x, y),
                    (x + binsize, y),
                    (x + binsize, y + binsize),
                    (x, y + binsize),
                    (x, y),
                ]
            )
        else:
            geo = Point((x, y))
        return geo

    contours = data.apply(create_geo, axis=1)
    props = pd.DataFrame({"contours": contours})
    props["area"] = binsize ** 2
    return props


def in_concave_hull(p: np.ndarray, concave_hull: Union[Polygon, MultiPolygon]) -> np.ndarray:
    """Test if points in `p` are in `concave_hull` using scipy.spatial Delaunay's find_simplex.

    Args:
        p: a `Nx2` coordinates of `N` points in `K` dimensions
        concave_hull: A polygon returned from the concave_hull function (the first value).

    Returns:

    """
    assert p.shape[1] == 2, "this function only works for two dimensional data points."

    res = [concave_hull.intersects(Point(i)) for i in p]

    return np.array(res)


def in_convex_hull(p: np.ndarray, convex_hull: Union[Delaunay, np.ndarray]) -> np.ndarray:
    """Test if points in `p` are in `convex_hull` using scipy.spatial Delaunay's find_simplex.

    Args:
        p: a `NxK` coordinates of `N` points in `K` dimensions
        convex_hull: either a scipy.spatial.Delaunay object or the `MxK` array of the coordinates of `M` points in `K`
              dimensions for which Delaunay triangulation will be computed.

    Returns:

    """
    assert p.shape[1] == convex_hull.shape[1], "the second dimension of p and hull must be the same."

    if not isinstance(convex_hull, Delaunay):
        hull = Delaunay(convex_hull)

    return hull.find_simplex(p) >= 0


def bin_matrix(X: Union[np.ndarray, spmatrix], binsize: int) -> Union[np.ndarray, csr_matrix]:
    """Bin a matrix.

    Args:
        X: Dense or sparse matrix.
        binsize: Bin size

    Returns:
        Dense or spares matrix, depending on what the input was.
    """
    shape = (math.ceil(X.shape[0] / binsize), math.ceil(X.shape[1] / binsize))

    def _bin_sparse(X):
        nz = X.nonzero()
        x, y = nz
        data = X[nz].A.flatten()
        x_bin = bin_indices(x, 0, binsize)
        y_bin = bin_indices(y, 0, binsize)
        return csr_matrix((data, (x_bin, y_bin)), shape=shape, dtype=X.dtype)

    def _bin_dense(X):
        binned = np.zeros(shape, dtype=X.dtype)
        for x in range(X.shape[0]):
            x_bin = bin_indices(x, 0, binsize)
            for y in range(X.shape[1]):
                y_bin = bin_indices(y, 0, binsize)
                binned[x_bin, y_bin] += X[x, y]
        return binned

    if issparse(X):
        return _bin_sparse(X)
    return _bin_dense(X)


def mapping_label(data: pd.DataFrame, label: np.ndarray, shifts: Optional[tuple] = None) -> pd.Series:
    """Mapping labels to data.
    Args:
        data: a data frame with columns 'x' and 'y'.
        label: cell segmentation label matrix.
        shifts: shifts of x and y. if not, will be calculated automatically.

    Returns:
        a pandas.Series
    """

    cell_coor = []
    if not shifts:
        shifts = (data['x'].min(), data['y'].min())

    # slow
    for i in range(label.shape[0]):
        for j in range(label.shape[1]):
            c = label[i, j]
            # background will be removed
            if c:
                cell_coor.append([i + shifts[0], j + shifts[1], c])
    cell_coor = pd.DataFrame(cell_coor, columns=['x', 'y', 'cell_name'])
    cell_data = pd.merge(data, cell_coor, how='left', on=['x', 'y'])
    cell_data = cell_data.dropna()
    cell_data['cell_name'] = cell_data['cell_name'].astype(str)

    return cell_data
