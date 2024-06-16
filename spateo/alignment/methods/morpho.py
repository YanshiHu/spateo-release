import random

import numpy as np
import ot
import torch
from anndata import AnnData

try:
    from typing import Any, Dict, List, Literal, Optional, Tuple, Union
except ImportError:
    from typing_extensions import Literal

from typing import List, Optional, Tuple, Union

from spateo.logging import logger_manager as lm

from .utils import (
    _chunk,
    _data,
    _dot,
    _identity,
    _linalg,
    _mul,
    _pi,
    _pinv,
    _power,
    _prod,
    _psi,
    _randperm,
    _roll,
    _unique,
    _unsqueeze,
    _init_guess_beta2,
    _init_guess_sigma2,
    align_preprocess,
    cal_dist,
    calc_exp_dissimilarity,
    coarse_rigid_alignment,
    empty_cache,
    get_optimal_R,
)


def con_K(
    X: Union[np.ndarray, torch.Tensor],
    Y: Union[np.ndarray, torch.Tensor],
    beta: Union[int, float] = 0.01,
    use_chunk: bool = False,
) -> Union[np.ndarray, torch.Tensor]:
    """con_K constructs the Squared Exponential (SE) kernel, where K(i,j)=k(X_i,Y_j)=exp(-beta*||X_i-Y_j||^2).

    Args:
        X: The first vector X\in\mathbb{R}^{N\times d}
        Y: The second vector X\in\mathbb{R}^{M\times d}
        beta: The length-scale of the SE kernel.
        use_chunk (bool, optional): Whether to use chunk to reduce the GPU memory usage. Note that if set to ``True'' it will slow down the calculation. Defaults to False.

    Returns:
        K: The kernel K\in\mathbb{R}^{N\times M}
    """

    assert X.shape[1] == Y.shape[1], "X and Y do not have the same number of features."
    nx = ot.backend.get_backend(X, Y)

    K = cal_dist(X, Y)
    K = nx.exp(-beta * K)
    return K


############
# BioAlign #
############
def get_P(
    XnAHat: Union[np.ndarray, torch.Tensor],
    XnB: Union[np.ndarray, torch.Tensor],
    sigma2: Union[int, float, np.ndarray, torch.Tensor],
    beta2: Union[int, float, np.ndarray, torch.Tensor],
    alpha: Union[np.ndarray, torch.Tensor],
    gamma: Union[float, np.ndarray, torch.Tensor],
    Sigma: Union[np.ndarray, torch.Tensor],
    GeneDistMat: Union[np.ndarray, torch.Tensor],
    SpatialDistMat: Union[np.ndarray, torch.Tensor],
    samples_s: Optional[List[float]] = None,
    outlier_variance: float = None,
) -> Tuple[Any, Any, Any]:
    """Calculating the generating probability matrix P.

    Args:
        XAHat: Current spatial coordinate of sample A. Shape: N x D.
        XnB : spatial coordinate of sample B (reference sample). Shape: M x D.
        sigma2: The spatial coordinate noise.
        beta2: The gene expression noise.
        alpha: A vector that encoding each probability generated by the spots of sample A. Shape: N x 1.
        gamma: Inlier proportion of sample A.
        Sigma: The posterior covariance matrix of Gaussian process. Shape: N x N or N x 1.
        GeneDistMat: The gene expression distance matrix between sample A and sample B. Shape: N x M.
        SpatialDistMat: The spatial coordinate distance matrix between sample A and sample B. Shape: N x M.
        samples_s: The space size of each sample. Area size for 2D samples and volume size for 3D samples.
    Returns:
        P: Generating probability matrix P. Shape: N x M.
    """

    assert XnAHat.shape[1] == XnB.shape[1], "XnAHat and XnB do not have the same number of features."
    assert XnAHat.shape[0] == alpha.shape[0], "XnAHat and alpha do not have the same length."
    assert XnAHat.shape[0] == Sigma.shape[0], "XnAHat and Sigma do not have the same length."

    nx = ot.backend.get_backend(XnAHat, XnB)
    NA, NB, D = XnAHat.shape[0], XnB.shape[0], XnAHat.shape[1]
    if samples_s is None:
        samples_s = nx.maximum(
            _prod(nx)(nx.max(XnAHat, axis=0) - nx.min(XnAHat, axis=0)),
            _prod(nx)(nx.max(XnB, axis=0) - nx.min(XnB, axis=0)),
        )
    outlier_s = samples_s * NA
    if outlier_variance is None:
        exp_SpatialMat = nx.exp(-SpatialDistMat / (2 * sigma2))
    else:
        exp_SpatialMat = nx.exp(-SpatialDistMat / (2 * sigma2 / outlier_variance))
    spatial_term1 = nx.einsum(
        "ij,i->ij",
        exp_SpatialMat,
        (_mul(nx)(alpha, nx.exp(-Sigma / sigma2))),
    )
    spatial_outlier = _power(nx)((2 * _pi(nx) * sigma2), _data(nx, D / 2, XnAHat)) * (1 - gamma) / (gamma * outlier_s)
    spatial_term2 = spatial_outlier + nx.einsum("ij->j", spatial_term1)
    spatial_P = spatial_term1 / _unsqueeze(nx)(spatial_term2, 0)
    spatial_inlier = 1 - spatial_outlier / (spatial_outlier + nx.einsum("ij->j", exp_SpatialMat))
    term1 = nx.einsum(
        "ij,i->ij",
        _mul(nx)(nx.exp(-SpatialDistMat / (2 * sigma2)), nx.exp(-GeneDistMat / (2 * beta2))),
        (_mul(nx)(alpha, nx.exp(-Sigma / sigma2))),
    )
    P = term1 / (_unsqueeze(nx)(nx.einsum("ij->j", term1), 0) + 1e-8)
    P = nx.einsum("j,ij->ij", spatial_inlier, P)

    term1 = nx.einsum(
        "ij,i->ij",
        nx.exp(-SpatialDistMat / (2 * sigma2)),
        (_mul(nx)(alpha, nx.exp(-Sigma / sigma2))),
    )
    sigma2_P = term1 / (_unsqueeze(nx)(nx.einsum("ij->j", term1), 0) + 1e-8)
    sigma2_P = nx.einsum("j,ij->ij", spatial_inlier, sigma2_P)
    return P, spatial_P, sigma2_P


def get_P_chunk(
    XnAHat: Union[np.ndarray, torch.Tensor],
    XnB: Union[np.ndarray, torch.Tensor],
    X_A: Union[np.ndarray, torch.Tensor],
    X_B: Union[np.ndarray, torch.Tensor],
    sigma2: Union[int, float, np.ndarray, torch.Tensor],
    beta2: Union[int, float, np.ndarray, torch.Tensor],
    alpha: Union[np.ndarray, torch.Tensor],
    gamma: Union[float, np.ndarray, torch.Tensor],
    Sigma: Union[np.ndarray, torch.Tensor],
    samples_s: Optional[List[float]] = None,
    outlier_variance: float = None,
    chunk_size: int = 1000,
    dissimilarity: str = "kl",
) -> Union[np.ndarray, torch.Tensor]:
    """Calculating the generating probability matrix P.

    Args:
        XAHat: Current spatial coordinate of sample A. Shape
    """
    # Get the number of cells in each sample
    NA, NB = XnAHat.shape[0], XnB.shape[0]
    # Get the number of genes
    G = X_A.shape[1]
    # Get the number of spatial dimensions
    D = XnAHat.shape[1]
    chunk_num = int(np.ceil(NA / chunk_size))

    assert XnAHat.shape[1] == XnB.shape[1], "XnAHat and XnB do not have the same number of features."
    assert XnAHat.shape[0] == alpha.shape[0], "XnAHat and alpha do not have the same length."
    assert XnAHat.shape[0] == Sigma.shape[0], "XnAHat and Sigma do not have the same length."

    nx = ot.backend.get_backend(XnAHat, XnB)
    if samples_s is None:
        samples_s = nx.maximum(
            _prod(nx)(nx.max(XnAHat, axis=0) - nx.min(XnAHat, axis=0)),
            _prod(nx)(nx.max(XnB, axis=0) - nx.min(XnB, axis=0)),
        )
    outlier_s = samples_s * NA
    # chunk
    X_Bs = _chunk(nx, X_B, chunk_num, dim=0)
    XnBs = _chunk(nx, XnB, chunk_num, dim=0)

    Ps = []
    for x_Bs, xnBs in zip(X_Bs, XnBs):
        SpatialDistMat = cal_dist(XnAHat, xnBs)
        GeneDistMat = calc_exp_dissimilarity(X_A=X_A, X_B=x_Bs, dissimilarity=dissimilarity)
        if outlier_variance is None:
            exp_SpatialMat = nx.exp(-SpatialDistMat / (2 * sigma2))
        else:
            exp_SpatialMat = nx.exp(-SpatialDistMat / (2 * sigma2 / outlier_variance))
        spatial_term1 = nx.einsum(
            "ij,i->ij",
            exp_SpatialMat,
            (_mul(nx)(alpha, nx.exp(-Sigma / sigma2))),
        )
        spatial_outlier = (
            _power(nx)((2 * _pi(nx) * sigma2), _data(nx, D / 2, XnAHat)) * (1 - gamma) / (gamma * outlier_s)
        )
        spatial_inlier = 1 - spatial_outlier / (spatial_outlier + nx.einsum("ij->j", exp_SpatialMat))
        term1 = nx.einsum(
            "ij,i->ij",
            _mul(nx)(nx.exp(-SpatialDistMat / (2 * sigma2)), nx.exp(-GeneDistMat / (2 * beta2))),
            (_mul(nx)(alpha, nx.exp(-Sigma / sigma2))),
        )
        P = term1 / (_unsqueeze(nx)(nx.einsum("ij->j", term1), 0) + 1e-8)
        P = nx.einsum("j,ij->ij", spatial_inlier, P)
        Ps.append(P)
    P = nx.concatenate(Ps, axis=1)
    return P


def BA_align(
    sampleA: AnnData,
    sampleB: AnnData,
    genes: Optional[Union[List, torch.Tensor]] = None,
    spatial_key: str = "spatial",
    key_added: str = "align_spatial",
    iter_key_added: Optional[str] = None,
    vecfld_key_added: Optional[str] = "VecFld_morpho",
    layer: str = "X",
    dissimilarity: str = "kl",
    use_rep: Optional[str] = None,
    keep_size: bool = False,
    max_iter: int = 200,
    lambdaVF: Union[int, float] = 1e2,
    beta: Union[int, float] = 0.01,
    K: Union[int, float] = 15,
    beta2: Optional[Union[int, float]] = None,
    beta2_end: Optional[Union[int, float]] = None,
    normalize_c: bool = True,
    normalize_g: bool = True,
    dtype: str = "float32",
    device: str = "cpu",
    inplace: bool = True,
    verbose: bool = True,
    nn_init: bool = True,
    allow_flip: bool = False,
    SVI_mode: bool = True,
    batch_size: int = 1000,
    partial_robust_level: float = 25,
    pre_compute_dist: bool = True,
) -> Tuple[Optional[Tuple[AnnData, AnnData]], np.ndarray, np.ndarray]:
    """The core function of Spateo alignment

    Args:
        sampleA: Sample A that acts as reference.
        sampleB: Sample B that performs alignment.
        genes: Genes used for calculation. If None, use all common genes for calculation.
        spatial_key: The key in ``.obsm`` that corresponds to the raw spatial coordinate.
        key_added: ``.obsm`` key under which to add the aligned spatial coordinate.
        iter_key_added: ``.uns`` key under which to add the result of each iteration of the iterative process. If ``iter_key_added`` is None, the results are not saved.
        vecfld_key_added: The key that will be used for the vector field key in ``.uns``. If ``vecfld_key_added`` is None, the results are not saved.
        layer: If ``'X'``, uses ``.X`` to calculate dissimilarity between spots, otherwise uses the representation given by ``.layers[layer]``.
        dissimilarity: Expression dissimilarity measure: ``'kl'``, ``'euclidean'``, or ``'cos'``.
        use_rep: Use the indicated representation. If use_rep is None, then use the given "layer", else use the key stored in .obsm. E.g., "X_pca".
        max_iter: Max number of iterations for morpho alignment. 
        lambdaVF : Hyperparameter that controls the non-rigid distortion degree. Smaller means more flexibility.
        beta: The length-scale of the SE kernel. Higher means more flexibility.
        K: The number of sparse inducing points used for Nystr ̈om approximation. Smaller means faster but less accurate.
        beta2: Manually assigned significance gene expression similarity. Smaller indicating greater significance.
        beta2_end: Manually assigned significance gene expression similarity. Smaller indicating greater significance.
        normalize_c: Whether to normalize spatial coordinates.
        normalize_g: Whether to normalize gene expression. If ``dissimilarity`` == ``'kl'``, ``normalize_g`` must be False.
        samples_s: The space size of each sample. Area size for 2D samples and volume size for 3D samples.
        dtype: The floating-point number type. Only ``float32`` and ``float64``.
        device: Equipment used to run the program. You can also set the specified GPU for running. ``E.g.: '0'``.
        inplace: Whether to copy adata or modify it inplace.
        verbose: If ``True``, print progress updates.
        nn_init: If ``True``, use nearest neighbor matching to initialize the alignment.
        SVI_mode: Whether to use stochastic variational inferential (SVI) optimization strategy.
        batch_size: The size of the mini-batch of SVI. If set smaller, the calculation will be faster, but it will affect the accuracy, and vice versa. If not set, it is automatically set to one-tenth of the data size.
        partial_robust_level: The robust level of partial alignment. The larger the value, the more robust the alignment to partial cases is. Recommended setting from 1 to 50.
        pre_compute_dist: If ``True``, the gene similarity matrix is computed before the mini batch is performed. Otherwise, it is computed during the mini batch. This can be significantly faster, but can also require more GPU memory if using GPU.
    """
    empty_cache(device=device)
    # Preprocessing
    normalize_g = False if dissimilarity == "kl" else normalize_g
    sampleA, sampleB = (sampleA, sampleB) if inplace else (sampleA.copy(), sampleB.copy())
    (
        nx,
        type_as,
        new_samples,
        exp_matrices,
        spatial_coords,
        normalize_scale_list,
        normalize_mean_list,
    ) = align_preprocess(
        samples=[sampleA, sampleB],
        layer=layer,
        genes=genes,
        spatial_key=spatial_key,
        normalize_c=normalize_c,
        normalize_g=normalize_g,
        dtype=dtype,
        device=device,
        verbose=verbose,
        use_rep=use_rep,
    )
    
    coordsA, coordsB = spatial_coords[1], spatial_coords[0]
    X_A, X_B = exp_matrices[1], exp_matrices[0]
    del spatial_coords, exp_matrices
    NA, NB, D, G = coordsA.shape[0], coordsB.shape[0], coordsA.shape[1], X_A.shape[1]
    sub_sample = False
    sub_sample_num = 20000
    if SVI_mode and (NA > sub_sample_num or NB > sub_sample_num) and (pre_compute_dist is False):
        if NA > sub_sample_num:
            sub_idx_A = np.random.choice(NA, sub_sample_num, replace=False)
            sub_coordsA = coordsA[sub_idx_A, :]
            sub_X_A = X_A[sub_idx_A, :]
        else:
            sub_coordsA = coordsA
            sub_X_A = X_A
        if NB > sub_sample_num:
            sub_idx_B = np.random.choice(NB, sub_sample_num, replace=False)
            sub_coordsB = coordsB[sub_idx_B, :]
            sub_X_B = X_B[sub_idx_B, :]
        else:
            sub_coordsB = coordsB
            sub_X_B = X_B

        GeneDistMat = calc_exp_dissimilarity(X_A=sub_X_A, X_B=sub_X_B, dissimilarity=dissimilarity)
        sub_sample = True
    else:
        GeneDistMat = calc_exp_dissimilarity(X_A=X_A, X_B=X_B, dissimilarity=dissimilarity)
    area = _prod(nx)(nx.max(coordsA, axis=0) - nx.min(coordsA, axis=0))

    if nn_init:
        # perform coarse rigid alignment
        if sub_sample:
            _cra_kwargs = dict(
                coordsA=sub_coordsA,
                coordsB=sub_coordsB,
                X_A=sub_X_A,
                X_B=sub_X_B,
                transformed_points=coordsA,
                allow_flip=allow_flip,
            )
        else:
            _cra_kwargs = dict(
                coordsA=coordsA,
                coordsB=coordsB,
                X_A=X_A,
                X_B=X_B,
                transformed_points=None,
                allow_flip=allow_flip,
            )
        coordsA, inlier_A, inlier_B, inlier_P, init_R, init_t = coarse_rigid_alignment(
            dissimilarity=dissimilarity, top_K=10, verbose=verbose, **_cra_kwargs
        )
        empty_cache(device=device)
        coordsA = _data(nx, coordsA, type_as)
        inlier_A = _data(nx, inlier_A, type_as)
        inlier_B = _data(nx, inlier_B, type_as)
        inlier_P = _data(nx, inlier_P, type_as)
    else:
        # init_R = np.eye(D)
        # init_t = np.zeros((D,))
        # inlier_A = np.zeros((4,D))
        # inlier_B = np.zeros((4,D))
        # inlier_P = np.ones((4,1))
        # init_R = _data(nx, init_R, type_as)
        # init_t = _data(nx, init_t, type_as)
        # inlier_A = _data(nx, inlier_A, type_as)
        # inlier_B = _data(nx, inlier_B, type_as)
        # inlier_P = _data(nx, inlier_P, type_as)

        init_R = nx.eye(D, type_as=type_as)
        init_t = nx.zeros((D,), type_as=type_as)
        inlier_A = nx.zeros((4,D), type_as=type_as)
        inlier_B = nx.zeros((4,D), type_as=type_as)
        inlier_P = nx.ones((4,1), type_as=type_as)
    coarse_alignment = coordsA

    # Random select control points
    Unique_coordsA = _unique(nx, coordsA, 0)
    idx = random.sample(range(Unique_coordsA.shape[0]), min(K, Unique_coordsA.shape[0]))
    ctrl_pts = Unique_coordsA[idx, :]
    K = ctrl_pts.shape[0]

    # construct the kernel
    GammaSparse = con_K(ctrl_pts, ctrl_pts, beta)
    U = con_K(coordsA, ctrl_pts, beta)
    kappa = nx.ones((NA), type_as=type_as)
    alpha = nx.ones((NA), type_as=type_as)
    VnA = nx.zeros(coordsA.shape, type_as=type_as)
    Coff = nx.zeros(ctrl_pts.shape, type_as=type_as)

    gamma, gamma_a, gamma_b = (
        _data(nx, 0.5, type_as),
        _data(nx, 1.0, type_as),
        _data(nx, 1.0, type_as),
    )
    minP, sigma2_terc, erc = (
        _data(nx, 1e-5, type_as),
        _data(nx, 1, type_as),
        _data(nx, 1e-4, type_as),
    )
    SigmaDiag = nx.zeros((NA), type_as=type_as)
    XAHat, RnA = coordsA, coordsA
    if sub_sample:
        SpatialDistMat = cal_dist(sub_coordsA, sub_coordsB)
        del sub_coordsA, sub_coordsB
    else:
        SpatialDistMat = cal_dist(XAHat, coordsB)

    # initial guess for sigma2 and beta2
    sigma2 = _init_guess_sigma2(XAHat, coordsB)
    beta2, beta2_end = _init_guess_beta2(nx, X_A, X_B, dissimilarity, partial_robust_level, beta2, beta2_end, verbose=verbose)
    beta2_decrease = _power(nx)(beta2_end / beta2, 1 / (50))

    R = _identity(nx, D, type_as)
    

    # Use smaller spatial variance to reduce tails
    outlier_variance = 1
    max_outlier_variance = partial_robust_level
    outlier_variance_decrease = _power(nx)(_data(nx, max_outlier_variance, type_as), 1 / (max_iter / 2))

    if SVI_mode:
        SVI_deacy = _data(nx, 10.0, type_as)
        # Select a random subset of data
        batch_size = min(max(int(NB / 10), batch_size), NB)
        randomidx = _randperm(nx)(NB)
        randIdx = randomidx[:batch_size]
        randomIdx = _roll(nx)(randomidx, batch_size)
        randcoordsB = coordsB[randIdx, :]  # batch_size x D
        if sub_sample:
            randGeneDistMat = calc_exp_dissimilarity(X_A=X_A, X_B=X_B[randIdx, :], dissimilarity=dissimilarity)
            SpatialDistMat = cal_dist(coordsA, randcoordsB)
        else:
            randGeneDistMat = GeneDistMat[:, randIdx]  # NA x batch_size
            SpatialDistMat = SpatialDistMat[:, randIdx]  # NA x batch_size
        Sp, Sp_spatial, Sp_sigma2 = 0, 0, 0
        SigmaInv = nx.zeros((K, K), type_as=type_as)  # K x K
        PXB_term = nx.zeros((NA, D), type_as=type_as)  # NA x D

    iteration = (
        lm.progress_logger(range(max_iter), progress_name="Start morpho alignment") if verbose else range(max_iter)
    )
    if iter_key_added is not None:
        sampleB.uns[iter_key_added] = dict()
        sampleB.uns[iter_key_added][key_added] = {}
        sampleB.uns[iter_key_added]["sigma2"] = {}
        sampleB.uns[iter_key_added]["beta2"] = {}

    for iter in iteration:
        if iter_key_added is not None:
            iter_XAHat = XAHat * normalize_scale_list[0] + normalize_mean_list[0] if normalize_c else XAHat
            sampleB.uns[iter_key_added][key_added][iter] = nx.to_numpy(iter_XAHat)
            sampleB.uns[iter_key_added]["sigma2"][iter] = nx.to_numpy(sigma2)
            sampleB.uns[iter_key_added]["beta2"][iter] = nx.to_numpy(beta2)
        if SVI_mode:
            step_size = nx.minimum(_data(nx, 1.0, type_as), SVI_deacy / (iter + 1.0))
            P, spatial_P, sigma2_P = get_P(
                XnAHat=XAHat,
                XnB=randcoordsB,
                sigma2=sigma2,
                beta2=beta2,
                alpha=alpha,
                gamma=gamma,
                Sigma=SigmaDiag,
                GeneDistMat=randGeneDistMat,
                SpatialDistMat=SpatialDistMat,
                outlier_variance=outlier_variance,
            )
        else:
            P, spatial_P, sigma2_P = get_P(
                XnAHat=XAHat,
                XnB=coordsB,
                sigma2=sigma2,
                beta2=beta2,
                alpha=alpha,
                gamma=gamma,
                Sigma=SigmaDiag,
                GeneDistMat=GeneDistMat,
                SpatialDistMat=SpatialDistMat,
                outlier_variance=outlier_variance,
            )

        if iter > 5:
            beta2 = (
                nx.maximum(beta2 * beta2_decrease, beta2_end)
                if beta2_decrease < 1
                else nx.minimum(beta2 * beta2_decrease, beta2_end)
            )
            outlier_variance = nx.minimum(outlier_variance * outlier_variance_decrease, max_outlier_variance)

        K_NA = nx.einsum("ij->i", P)
        K_NB = nx.einsum("ij->j", P)
        K_NA_spatial = nx.einsum("ij->i", spatial_P)
        K_NB_spatial = nx.einsum("ij->j", spatial_P)
        K_NA_sigma2 = nx.einsum("ij->i", sigma2_P)
        K_NB_sigma2 = nx.einsum("ij->j", sigma2_P)

        # Update gamma
        if SVI_mode:
            Sp = step_size * nx.einsum("ij->", P) + (1 - step_size) * Sp
            Sp_spatial = step_size * nx.einsum("ij->", spatial_P) + (1 - step_size) * Sp_spatial
            Sp_sigma2 = step_size * nx.einsum("ij->", sigma2_P) + (1 - step_size) * Sp_sigma2
            gamma = nx.exp(_psi(nx)(gamma_a + Sp_spatial) - _psi(nx)(gamma_a + gamma_b + batch_size))
        else:
            Sp = nx.einsum("ij->", P)
            Sp_spatial = nx.einsum("ij->", spatial_P)
            Sp_sigma2 = nx.einsum("ij->", sigma2_P)
            gamma = nx.exp(_psi(nx)(gamma_a + Sp_spatial) - _psi(nx)(gamma_a + gamma_b + NB))
        gamma = _data(nx, 0.99, type_as) if gamma > 0.99 else gamma
        gamma = _data(nx, 0.01, type_as) if gamma < 0.01 else gamma

        # Update alpha
        alpha = step_size * nx.exp(_psi(nx)(kappa + K_NA_spatial) - _psi(nx)(kappa * NA + Sp_spatial)) + (1 - step_size) * alpha

        # Update VnA
        if (sigma2 < 0.015) or (iter > 80):
            if SVI_mode:
                SigmaInv = (
                    step_size * (sigma2 * lambdaVF * GammaSparse + _dot(nx)(U.T, nx.einsum("ij,i->ij", U, K_NA)))
                    + (1 - step_size) * SigmaInv
                )
                term1 = _dot(nx)(_pinv(nx)(SigmaInv), U.T)
                PXB_term = (
                    step_size * (_dot(nx)(P, randcoordsB) - nx.einsum("ij,i->ij", RnA, K_NA))
                    + (1 - step_size) * PXB_term
                )
                Coff = _dot(nx)(term1, PXB_term)
                VnA = _dot(nx)(
                    U,
                    Coff,
                )
                SigmaDiag = sigma2 * nx.einsum("ij->i", nx.einsum("ij,ji->ij", U, term1))
            else:
                term1 = _dot(nx)(
                    _pinv(nx)(sigma2 * lambdaVF * GammaSparse + _dot(nx)(U.T, nx.einsum("ij,i->ij", U, K_NA))),
                    U.T,
                )
                SigmaDiag = sigma2 * nx.einsum("ij->i", nx.einsum("ij,ji->ij", U, term1))
                Coff = _dot(nx)(term1, (_dot(nx)(P, coordsB) - nx.einsum("ij,i->ij", RnA, K_NA)))
                VnA = _dot(nx)(
                    U,
                    Coff,
                )

        # Update R()
        if nn_init:
            lambdaReg = partial_robust_level * 1e0 * Sp / nx.sum(inlier_P)
        else:
            lambdaReg = 0
        if SVI_mode:
            PXA, PVA, PXB = (
                _dot(nx)(K_NA, coordsA)[None, :],
                _dot(nx)(K_NA, VnA)[None, :],
                _dot(nx)(K_NB, randcoordsB)[None, :],
            )
        else:
            PXA, PVA, PXB = (
                _dot(nx)(K_NA, coordsA)[None, :],
                _dot(nx)(K_NA, VnA)[None, :],
                _dot(nx)(K_NB, coordsB)[None, :],
            )
        PCYC, PCXC = _dot(nx)(inlier_P.T, inlier_B), _dot(nx)(inlier_P.T, inlier_A)
        if SVI_mode and iter > 1:
            t = (
                step_size
                * (
                    ((PXB - PVA - _dot(nx)(PXA, R.T)) + 2 * lambdaReg * sigma2 * (PCYC - _dot(nx)(PCXC, R.T)))
                    / (Sp + 2 * lambdaReg * sigma2 * nx.sum(inlier_P))
                )
                + (1 - step_size) * t
            )
        else:
            t = ((PXB - PVA - _dot(nx)(PXA, R.T)) + 2 * lambdaReg * sigma2 * (PCYC - _dot(nx)(PCXC, R.T))) / (
                Sp + 2 * lambdaReg * sigma2 * nx.sum(inlier_P)
            )
        if SVI_mode:
            A = -(
                _dot(nx)(PXA.T, t)
                + _dot(nx)(coordsA.T, nx.einsum("ij,i->ij", VnA, K_NA) - _dot(nx)(P, randcoordsB))
                + 2
                * lambdaReg
                * sigma2
                * (_dot(nx)(PCXC.T, t) - _dot(nx)(nx.einsum("ij,i->ij", inlier_A, inlier_P[:, 0]).T, inlier_B))
            ).T
        else:
            A = -(
                _dot(nx)(PXA.T, t)
                + _dot(nx)(coordsA.T, nx.einsum("ij,i->ij", VnA, K_NA) - _dot(nx)(P, coordsB))
                + 2
                * lambdaReg
                * sigma2
                * (_dot(nx)(PCXC.T, t) - _dot(nx)(nx.einsum("ij,i->ij", inlier_A, inlier_P[:, 0]).T, inlier_B))
            ).T

        svdU, svdS, svdV = _linalg(nx).svd(A)
        C = _identity(nx, D, type_as)
        C[-1, -1] = _linalg(nx).det(_dot(nx)(svdU, svdV))
        if SVI_mode and iter > 1:
            R = step_size * (_dot(nx)(_dot(nx)(svdU, C), svdV)) + (1 - step_size) * R
        else:
            R = _dot(nx)(_dot(nx)(svdU, C), svdV)
        RnA = _dot(nx)(coordsA, R.T) + t
        XAHat = RnA + VnA

        # Update sigma2 and beta2
        if SVI_mode:
            SpatialDistMat = cal_dist(XAHat, randcoordsB)
        else:
            SpatialDistMat = cal_dist(XAHat, coordsB)
        sigma2_old = sigma2
        sigma2 = nx.maximum(
            (
                nx.einsum("ij,ij", sigma2_P, SpatialDistMat) / (D * Sp_sigma2)
                + nx.einsum("i,i", K_NA_sigma2, SigmaDiag) / Sp_sigma2
            ),
            _data(nx, 1e-3, type_as),
        )
        sigma2_terc = nx.abs((sigma2 - sigma2_old) / sigma2)

        # Next batch
        if SVI_mode and iter < max_iter - 1:
            randIdx = randomidx[:batch_size]
            randomidx = _roll(nx)(randomidx, batch_size)
            randcoordsB = coordsB[randIdx, :]
            if sub_sample:
                randGeneDistMat = calc_exp_dissimilarity(X_A=X_A, X_B=X_B[randIdx, :], dissimilarity=dissimilarity)
            else:
                randGeneDistMat = GeneDistMat[:, randIdx]  # NA x batch_size
            SpatialDistMat = cal_dist(XAHat, randcoordsB)
        empty_cache(device=device)

    # full data
    if SVI_mode:
        P = get_P_chunk(
            XnAHat=XAHat,
            XnB=coordsB,
            X_A=X_A,
            X_B=X_B,
            sigma2=sigma2,
            beta2=beta2,
            alpha=alpha,
            gamma=gamma,
            Sigma=SigmaDiag,
            outlier_variance=outlier_variance,
        )
    # Get optimal Rigid transformation
    optimal_RnA, optimal_R, optimal_t = get_optimal_R(
        coordsA=coordsA,
        coordsB=coordsB,
        P=P,
        R_init=R,
    )

    if verbose:
        lm.main_info(f"Key Parameters: gamma: {gamma}; beta2: {beta2}; sigma2: {sigma2}")

    if keep_size:
        area_after = _prod(nx)(nx.max(XAHat, axis=0) - nx.min(XAHat, axis=0))
        XAHat = XAHat * (area / area_after)

    if normalize_c:
        XAHat = XAHat * normalize_scale_list[0] + normalize_mean_list[0]
        RnA = RnA * normalize_scale_list[0] + normalize_mean_list[0]
        optimal_RnA = optimal_RnA * normalize_scale_list[0] + normalize_mean_list[0]
        coarse_alignment = coarse_alignment * normalize_scale_list[0] + normalize_mean_list[0]

    # Save aligned coordinates
    sampleB.obsm["Nonrigid_align_spatial"] = nx.to_numpy(XAHat).copy()
    sampleB.obsm["Rigid_align_spatial"] = nx.to_numpy(optimal_RnA).copy()

    # save vector field and other parameters
    if not (vecfld_key_added is None):
        sampleB.uns[vecfld_key_added] = {
            "R": nx.to_numpy(R),
            "t": nx.to_numpy(t),
            "optimal_R": nx.to_numpy(optimal_R),
            "optimal_t": nx.to_numpy(optimal_t),
            "init_R": init_R,
            "init_t": init_t,
            "beta": beta,
            "Coff": nx.to_numpy(Coff),
            "ctrl_pts": nx.to_numpy(ctrl_pts),
            "normalize_scale": nx.to_numpy(normalize_scale_list[0]) if normalize_c else None,
            "normalize_mean_list": [nx.to_numpy(normalize_mean) for normalize_mean in normalize_mean_list]
            if normalize_c
            else None,
            "normalize_c": normalize_c,
            "dissimilarity": dissimilarity,
            "beta2": nx.to_numpy(sigma2),
            "sigma2": nx.to_numpy(sigma2),
            "gamma": nx.to_numpy(gamma),
            "NA": NA,
            "outlier_variance": nx.to_numpy(outlier_variance),
        }
    empty_cache(device=device)
    return (
        None if inplace else (sampleA, sampleB),
        nx.to_numpy(P.T),
        nx.to_numpy(sigma2),
    )
