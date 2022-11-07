"""Written by @Jinerhal, adapted by @Xiaojieqiu.
"""
from typing import Dict, Optional, Tuple, Union

import cv2
import numpy as np
from anndata import AnnData

from ...configuration import SKM
from .utils import save_return_show_fig_utils


@SKM.check_adata_is_type(SKM.ADATA_UMI_TYPE, "adata")
def spatial_domains(
    adata: AnnData,
    bin_size: Optional[int] = None,
    spatial_key: str = "spatial",
    label_key: str = "cluster_img_label",
    use_scale: bool = True,
    absolute: bool = False,
    swap_x_y: bool = False,
    background: Union[None, str] = None,
    save_show_or_return: str = "show",
    save_kwargs: Dict = {},
    **kwargs,
) -> None:
    """Generate an image with contours of each spatial domains.

    Args:
        adata: The adata object used to create the image.
        bin_size: The size of the binning. Default to None.
        spatial_key: The key name of the spatial coordinates. Default to "spatial".
        label_key: The key name of the image label values. Default to "cluster_img_label".
        use_scale: Whether or not to plot in physical units. Only valid when
            appropriate scale keys are present in .uns
        absolute: Whether to set the axes to be in absolute coordinates. By
            default, relative coordinates are used (i.e. the axes start at
            zero).
        swap_x_y: Whether or not to swap the x, y values when using `pl.imshow`.
        background: string or None (optional, default 'None`)
            The color of the background. Usually this will be either
            'white' or 'black', but any color name will work. Ideally
            one wants to match this appropriately to the colors being
            used for points etc. This is one of the things that themes
            handle for you. Note that if theme
            is passed then this value will be overridden by the
            corresponding option of the theme.
        show: Visualize the result. Default to True.
        save_show_or_return: `str` {'save', 'show', 'return', 'both', 'all'} (default: `show`)
            Whether to save, show or return the figure. If "both", it will save and plot the figure at the same time. If
            "all", the figure will be saved, displayed and the associated axis and other object will be return.
        save_kwargs: `dict` (default: `{}`)
            A dictionary that will passed to the save_fig function. By default it is an empty dictionary and the
            save_fig function will use the {"path": None, "prefix": 'scatter', "dpi": None, "ext": 'pdf', "transparent":
            True, "close": True, "verbose": True} as its parameters. Otherwise you can provide a dictionary that
            properly modify those keys according to your needs.
        **kwargs: Additional keyword arguments are all passed to :func:`imshow`.
    """
    import matplotlib.pyplot as plt
    from matplotlib import rcParams
    from matplotlib.colors import to_hex

    label_list = np.unique(adata.obs[label_key])
    labels = np.zeros(len(adata))
    for i in range(len(label_list)):
        labels[adata.obs[label_key] == label_list[i]] = i + 1

    if bin_size is None:
        bin_size = adata.uns["bin_size"]

    label_img = np.zeros(
        (
            int(max(adata.obsm[spatial_key][:, 0] // bin_size)) + 1,
            int(max(adata.obsm[spatial_key][:, 1] // bin_size)) + 1,
        )
    )
    for i in range(len(adata)):
        label_img[
            int(adata.obsm[spatial_key][i, 0] // bin_size), int(adata.obsm[spatial_key][i, 1] // bin_size)
        ] = labels[i]

    contour_img = label_img.copy()
    contour_img[:, :] = 255
    for i in np.unique(label_img):
        if i == 0:
            continue
        label_img_gray = np.where(label_img == i, 0, 1).astype("uint8")
        _, thresh = cv2.threshold(label_img_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        contour, _ = cv2.findContours(thresh, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
        contour_img = cv2.drawContours(contour_img, contour[:], -1, 0.5, 1)

    fig, ax = plt.subplots(figsize=(5, 5), tight_layout=True)
    kwargs.update({"cmap": "Blues"})

    if swap_x_y:
        im = ax.imshow(contour_img.T, **kwargs)
    else:
        im = ax.imshow(contour_img, **kwargs)

    ax.set_title(f"domain contour ({label_key})")

    unit = SKM.get_uns_spatial_attribute(adata, SKM.UNS_SPATIAL_SCALE_UNIT_KEY)
    adata_bounds = SKM.get_agg_bounds(adata)
    # Note that we +1 to the xmax and ymax values because the first and last
    # ticks are at exactly these locations.
    extent = (
        [adata_bounds[0], adata_bounds[1] + 1, adata_bounds[3] + 1, adata_bounds[2]]
        if absolute
        else [0, contour_img.shape[1], contour_img.shape[0], 0]
    )

    xlabel, ylabel = "Y", "X"
    if use_scale and unit is not None:
        binsize = SKM.get_uns_spatial_attribute(adata, SKM.UNS_SPATIAL_BINSIZE_KEY)
        scale = SKM.get_uns_spatial_attribute(adata, SKM.UNS_SPATIAL_SCALE_KEY) * binsize
        extent = [val * scale for val in extent]
        xlabel += f" ({unit})"
        ylabel += f" ({unit})"

    im.set_extent(tuple(extent))
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)

    if background is None:
        _background = rcParams.get("figure.facecolor")
        _background = to_hex(_background) if type(_background) is tuple else _background
        # if save_show_or_return != 'save': set_figure_params('dynamo', background=_background)
    else:
        _background = background

    return save_return_show_fig_utils(
        save_show_or_return=save_show_or_return,
        show_legend=False,
        background=_background,
        prefix="spatial_domains_contours",
        save_kwargs=save_kwargs,
        total_panels=1,
        fig=fig,
        axes=ax,
        return_all=False,
        return_all_list=None,
    )
