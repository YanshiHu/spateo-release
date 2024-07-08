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

from spateo.alignment.methods.utils import (  # sample,; _sparse_concat,
    _data,
    _dot,
    _get_anneling_factor,
    _identity,
    _init_guess_sigma2,
    _linalg,
    _pinv,
    _prod,
    _psi,
    _randperm,
    _roll,
    _split,
    _unique,
    _unsqueeze,
    calc_distance,
    check_backend,
    check_label_transfer,
    check_label_transfer_dict,
    check_obs,
    check_rep_layer,
    check_spatial_coords,
    con_K,
    filter_common_genes,
    get_P_core,
    get_rep,
    inlier_from_NN,
    intersect_lsts,
    voxel_data,
)
from spateo.logging import logger_manager as lm


class Morpho_pairwise:
    def __init__(
        self,
        sampleA: AnnData,
        sampleB: AnnData,
        rep_layer: Union[str, List[str]] = "X",
        rep_field: Union[str, List[str]] = "layer",
        genes: Optional[Union[List[str], torch.Tensor]] = None,
        spatial_key: str = "spatial",
        key_added: str = "align_spatial",
        iter_key_added: Optional[str] = None,
        save_concrete_iter: bool = False,
        vecfld_key_added: Optional[str] = None,
        dissimilarity: Union[str, List[str]] = "kl",
        probability_type: Union[str, List[str]] = "gauss",
        probability_parameters: Optional[Union[float, List[float]]] = None,
        label_transfer_dict: Optional[Union[dict, List[dict]]] = None,
        nn_init: bool = True,
        allow_flip: bool = False,
        init_layer: str = "X",
        init_field: str = "layer",
        max_iter: int = 200,  # put to run
        SVI_mode: bool = True,
        batch_size: int = 1000,
        pre_compute_dist: bool = True,
        sparse_calculation_mode: bool = False,
        sparse_top_k: int = 1024,
        lambdaVF: Union[int, float] = 1e2,
        beta: Union[int, float] = 0.01,
        K: Union[int, float] = 15,
        kernel_type: str = "euc",
        sigma2_init_scale: Optional[Union[int, float]] = 0.1,
        partial_robust_level: float = 25,
        normalize_c: bool = True,
        normalize_g: bool = True,
        dtype: str = "float32",
        device: str = "cpu",
        verbose: bool = True,
        guidance_pair: Optional[Union[List[np.ndarray], np.ndarray]] = None,
        guidance_effect: Optional[Union[bool, str]] = False,
        guidance_epsilon: float = 1,
        use_chunk: bool = False,
        chunk_capacity: float = 1.0,
        lambdaReg: float = 1.0,
    ) -> None:

        # initialization
        self.verbose = verbose

        # the order is different
        self.sampleB = sampleA  # sample A is data points (or self.sampleB)
        self.sampleA = sampleB  # sample B is model points (or self.sampleA)
        self.rep_layer = rep_layer
        self.rep_field = rep_field
        self.genes = genes
        self.spatial_key = spatial_key
        self.key_added = key_added
        self.iter_key_added = iter_key_added
        self.save_concrete_iter = save_concrete_iter
        self.vecfld_key_added = vecfld_key_added
        self.dissimilarity = dissimilarity
        self.probability_type = probability_type
        self.probability_parameters = probability_parameters
        self.label_transfer_dict = label_transfer_dict
        self.nn_init = nn_init
        self.max_iter = max_iter
        self.allow_flip = allow_flip
        self.init_layer = init_layer
        self.init_field = init_field
        self.SVI_mode = SVI_mode
        self.batch_size = batch_size
        self.pre_compute_dist = pre_compute_dist
        self.sparse_calculation_mode = sparse_calculation_mode
        self.sparse_top_k = sparse_top_k
        self.beta = beta
        self.lambdaVF = lambdaVF
        self.K = K
        self.kernel_type = kernel_type
        self.kernel_bandwidth = beta
        self.sigma2_init_scale = sigma2_init_scale
        self.partial_robust_level = partial_robust_level
        self.normalize_c = normalize_c
        self.normalize_g = normalize_g
        self.dtype = dtype
        self.device = device
        self.guidance_pair = guidance_pair
        self.guidance_effect = guidance_effect
        self.guidance_epsilon = guidance_epsilon
        self.use_chunk = use_chunk
        self.chunk_capacity = chunk_capacity
        self.lambdaReg = lambdaReg

        # checking keys
        self._check()

        # preprocessing and extract the core data matrices for the alignment
        self._align_preprocess(
            dtype=dtype,
            device=device,
        )

        self._construct_kernel(inducing_variables_num=K, sampling_method="random")

        self._initialize_variational_variables(sigma2_init_scale=sigma2_init_scale)

    def run(
        self,
    ):
        if self.nn_init:
            self._coarse_rigid_alignment()

        # calculate the representation(s) pairwise distance matrix if pre_compute_dist is True or not in SVI mode
        # this will reduce the runtime but consume more GPU memory
        if (not self.SVI_mode) or (self.pre_compute_dist):
            self.exp_layer_dist = calc_distance(
                X=self.exp_layers_A, Y=self.exp_layers_B, metric=self.dissimilarity, label_transfer=self.label_transfer
            )

        if self.iter_key_added is not None:
            self.iter_added = dict()
            self.iter_added[self.key_added] = {}
            self.iter_added["sigma2"] = {}
            # sampleB.uns[iter_key_added]["beta2"] = {}

        # start iteration
        iteration = (
            lm.progress_logger(range(self.max_iter), progress_name="Start Spateo pairwise alignment")
            if self.verbose
            else range(self.max_iter)
        )
        for iter in iteration:
            if self.iter_key_added is not None:
                self._save_iter(iter=iter)
            if self.SVI_mode:
                self._update_batch(iter=iter)
            self._update_assignment_P()
            self._update_gamma()
            self._update_alpha()
            if (iter > 80) or (self.sigma2 < 0.015) or (self.nonrigid_flag):
                self.nonrigid_flag = True
                self._update_nonrigid()
            self._update_rigid()
            self.XAHat = self.VnA + self.RnA
            self._update_sigma2(iter=iter)

        # get the full cell-cell assignment
        if self.SVI_mode:
            self.SVI_mode = False
            self._update_assignment_P()

        self._get_optimal_R()

        if self.verbose:
            lm.main_info(
                f"Key Parameters: gamma: {self.gamma}; sigma2: {self.sigma2}; probability_parameters: {self.probability_parameters}"
            )

        self._wrap_output()

        return self.P

    def _check(
        self,
    ):

        # Check if the representation is in the AnnData objects
        if self.rep_layer is not None:
            if self.rep_field is None:
                self.rep_field = "layer"

            if isinstance(self.rep_layer, str):
                self.rep_layer = [self.rep_layer]

            if isinstance(self.rep_field, str):
                self.rep_field = [self.rep_field] * len(self.rep_layer)

            if not check_rep_layer(
                samples=[self.sampleA, self.sampleB], rep_layer=self.rep_layer, rep_field=self.rep_field
            ):
                raise ValueError(f"The specified representation is not found in the attribute of the AnnData objects.")

            self.obs_key = check_obs(self.rep_layer, self.rep_field)
        else:
            raise ValueError(
                "No representation input is detected, which may not produce meaningful result. Please check the rep_layer and rep_field."
            )

        # Check spatial key
        if self.spatial_key not in self.sampleA.obsm:
            raise KeyError(f"Spatial key '{self.spatial_key}' not found in sampleA AnnData object.")
        if self.spatial_key not in self.sampleB.obsm:
            raise KeyError(f"Spatial key '{self.spatial_key}' not found in sampleB AnnData object.")

        # Check transfer proir
        if self.obs_key is not None:
            self.catA = self.sampleA.obs[self.obs_key].cat.categories.tolist()
            self.catB = self.sampleB.obs[self.obs_key].cat.categories.tolist()
            check_label_transfer_dict(self.catA, self.catB, self.label_transfer_dict)

        # Check dissimilarity
        if self.dissimilarity is None:
            self.dissimilarity = "kl"

        # dissimilarity should have the same number of layer as rep_layer
        if isinstance(self.dissimilarity, str):
            self.dissimilarity = [self.dissimilarity] * len(self.rep_layer)

        # Check each dissimilarity metric
        valid_metrics = ["kl", "sym_kl", "euc", "euclidean", "square_euc", "square_euclidean", "cos", "cosine", "label"]
        self.dissimilarity = [d_s.lower() for d_s in self.dissimilarity]  # Convert all to lowercase

        for d_s in self.dissimilarity:
            if d_s not in valid_metrics:
                raise ValueError(
                    f"Invalid `metric` value: {d_s}. Available `metrics` are: " f"{', '.join(valid_metrics)}."
                )

        # Check probability_type
        if self.probability_type is None:
            self.probability_type = "gauss"

        # probability_type should have the same number of layer as rep_layer
        if isinstance(self.probability_type, str):
            self.probability_type = [self.probability_type] * len(self.rep_layer)

        # Check each probability_type
        valid_metrics = [
            "gauss",
            "gaussian",
            "cos",
            "cosine",
            "label",
        ]
        self.probability_type = [d_s.lower() for d_s in self.probability_type]  # Convert all to lowercase

        for p_t in self.probability_type:
            if p_t not in valid_metrics:
                raise ValueError(
                    f"Invalid `metric` value: {p_t}. Available `metrics` are: " f"{', '.join(valid_metrics)}."
                )

        # Check probability_parameters
        if self.probability_parameters is None:
            self.probability_parameters = [None] * len(self.rep_layer)

        # Check init_layer and init_field
        if self.nn_init:
            # if isinstance(self.init_layer, str):
            #     self.init_layer = [self.init_layer]
            # if isinstance(self.init_field, str):
            #     self.init_field = [self.init_field]

            if not check_rep_layer(
                samples=[self.sampleA, self.sampleB],
                rep_layer=[self.init_layer],
                rep_field=[self.init_field],
            ):
                raise ValueError(f"The specified representation is not found in the attribute of the AnnData objects.")

        # Check guidance_effect
        if self.guidance_effect:
            valid_guidance_effects = ["nonrigid", "rigid", "both"]
            if self.guidance_effect not in valid_guidance_effects:
                raise ValueError(
                    f"Invalid `guidance_effect` value: {self.guidance_effect}. Available `guidance_effect` values are: "
                    f"{', '.join(valid_guidance_effects)}."
                )

    def _align_preprocess(
        self,
        dtype: str = "float32",
        device: str = "cpu",
    ):

        # Determine if gpu or cpu is being used
        (self.nx, self.type_as) = check_backend(device=device, dtype=dtype)

        # Get the common genes
        all_samples_genes = [self.sampleA[0].var.index, self.sampleB[0].var.index]
        common_genes = filter_common_genes(*all_samples_genes, verbose=self.verbose)
        self.genes = common_genes if self.genes is None else intersect_lsts(common_genes, self.genes)

        # Extract the gene expression / representations of all samples, where each representation has a layer
        self.exp_layers_A = []
        if self.rep_layer is not None:
            for rep, rep_f in zip(self.rep_layer, self.rep_field):
                self.exp_layers_A.append(
                    get_rep(
                        nx=self.nx,
                        type_as=self.type_as,
                        sample=self.sampleA,
                        rep=rep,
                        rep_field=rep_f,
                        genes=self.genes,
                    )
                )

        self.exp_layers_B = []
        if self.rep_layer is not None:
            for rep, rep_f in zip(self.rep_layer, self.rep_field):
                self.exp_layers_B.append(
                    get_rep(
                        nx=self.nx,
                        type_as=self.type_as,
                        sample=self.sampleB,
                        rep=rep,
                        rep_field=rep_f,
                        genes=self.genes,
                    )
                )

        # check the label tranfer dictionary and generate a matrix that contains the label transfer cost and cast to the specified type
        if self.obs_key is not None:
            self.label_transfer = check_label_transfer(
                self.nx, self.type_as, [self.sampleA, self.sampleB], self.obs_key, self.label_transfer_dict
            )
        else:
            self.label_transfer = None

        # Extract the spatial coordinates of samples
        self.coordsA = self.nx.from_numpy(
            check_spatial_coords(sample=self.sampleA, spatial_key=self.spatial_key), type_as=self.type_as
        )
        self.coordsB = self.nx.from_numpy(
            check_spatial_coords(sample=self.sampleB, spatial_key=self.spatial_key), type_as=self.type_as
        )

        # check the spatial coordinates dimensionality
        assert (
            self.coordsA.shape[1] == self.coordsB.shape[1]
        ), "Spatial coordinate dimensions are different, please check again."
        self.NA, self.NB, self.D = self.coordsA.shape[0], self.coordsB.shape[0], self.coordsA.shape[1]

        # Normalize spatial coordinates if required
        if self.normalize_c:
            self._normalize_coords()

        # Normalize gene expression if required
        if self.normalize_g:
            self._normalize_exps()

        # preprocess guidance pair if provided
        if (self.guidance_pair is not None) and (self.guidance_effect != False) and (self.guidance_epsilon > 0):
            self._guidance_pair_preprocess()
        else:
            self.guidance = False

        if self.verbose:
            lm.main_info(message=f"Preprocess finished.", indent_level=1)

    def _guidance_pair_preprocess(
        self,
    ):
        # Convert guidance pairs to the backend type
        self.X_BI = self.nx.from_numpy(self.guidance_pair[0], type_as=self.type_as)
        self.X_AI = self.nx.from_numpy(self.guidance_pair[1], type_as=self.type_as)

        if self.normalize_c:
            # Normalize the guidance pairs
            self.X_AI = (self.X_AI - self.normalize_means[0]) / self.normalize_scales[0]
            self.X_BI = (self.X_BI - self.normalize_means[1]) / self.normalize_scales[1]

    def _normalize_coords(
        self,
        separate_mean: bool = True,
        separate_scale: bool = False,
    ):
        normalize_scales = self.nx.zeros((2,), type_as=self.type_as)
        normalize_means = self.nx.zeros((2, self.D), type_as=self.type_as)

        coords = [self.coordsA, self.coordsB]
        # get the means for each coords
        for i in range(len(coords)):
            normalize_mean = self.nx.einsum("ij->j", coords[i]) / coords[i].shape[0]
            normalize_means[i] = normalize_mean

        # get the global means for whole coords if "separate_mean" is True
        if not separate_mean:
            global_mean = self.nx.mean(normalize_means, axis=0)
            normalize_means = self.nx.full((len(coords), self.D), global_mean)

        # move each coords to zero center and calculate the normalization scale
        for i in range(len(coords)):
            coords[i] -= normalize_means[i]
            normalize_scale = self.nx.sqrt(
                self.nx.einsum("ij->", self.nx.einsum("ij,ij->ij", coords[i], coords[i])) / coords[i].shape[0]
            )
            normalize_scales[i] = normalize_scale

        # get the global scale for whole coords if "separate_scale" is True
        if not separate_scale:
            global_scale = self.nx.mean(normalize_scales)
            normalize_scales = self.nx.full((len(coords),), global_scale)

        # normalize the scale of the coords
        for i in range(len(coords)):
            coords[i] /= normalize_scales[i]

        self.normalize_scales = normalize_scales
        self.normalize_means = normalize_means

        # show the normalization results if "verbose" is True
        if self.verbose:
            lm.main_info(message=f"Spatial coordinates normalization params:", indent_level=1)
            lm.main_info(message=f"Scale: {normalize_scales[:2]}...", indent_level=2)
            lm.main_info(message=f"Scale: {normalize_means[:2]}...", indent_level=2)

    def _normalize_exps(
        self,
    ):
        exp_layers = [self.exp_layers_A, self.exp_layers_B]

        for i, (rep_f, d_s) in enumerate(zip(self.rep_field, self.dissimilarity)):
            # only normalize the matrix if representation is layer and dissimilarity metric is not kl
            if (rep_f == "layer") and (d_s != "kl"):
                normalize_scale = 0

                # Calculate the normalization scale

                for l in range(len(exp_layers)):
                    normalize_scale += self.nx.sqrt(
                        self.nx.einsum("ij->", self.nx.einsum("ij,ij->ij", exp_layers[i][l], exp_layers[i][l]))
                        / exp_layers[i][l].shape[0]
                    )

                normalize_scale /= len(exp_layers)

                # Apply the normalization scale
                for l in range(len(exp_layers)):
                    exp_layers[i][l] /= normalize_scale

                if self.verbose:
                    lm.main_info(message=f"Gene expression normalization params:", indent_level=1)
                    lm.main_info(message=f"Scale: {normalize_scale}.", indent_level=2)

    def _initialize_variational_variables(
        self,
        sigma2_init_scale,
    ):
        # initial guess for sigma2, beta2, anneling factor for sigma2 and beta2
        self.sigma2 = sigma2_init_scale * _init_guess_sigma2(self.coordsA, self.coordsB)

        self._init_probability_parameters()

        self.sigma2_variance = 1
        self.sigma2_variance_end = self.partial_robust_level
        self.sigma2_variance_decress = _get_anneling_factor(
            start=self.sigma2_variance,
            end=self.sigma2_variance_end,
            iter=(self.max_iter / 2),
            nx=self.nx,
            type_as=self.type_as,
        )

        self.kappa = self.nx.ones((self.NA), type_as=self.type_as)
        self.alpha = self.nx.ones((self.NA), type_as=self.type_as)
        self.gamma, self.gamma_a, self.gamma_b = (
            _data(self.nx, 0.5, self.type_as),
            _data(self.nx, 1.0, self.type_as),
            _data(self.nx, 1.0, self.type_as),
        )
        self.VnA = self.nx.zeros(self.coordsA.shape, type_as=self.type_as)  # nonrigid vector velocity
        self.XAHat, self.RnA = self.coordsA, self.coordsA  # initial transformed / rigid position
        self.Coff = self.nx.zeros(self.K, type_as=self.type_as)  # inducing variables coefficient
        self.SigmaDiag = self.nx.zeros((self.NA), type_as=self.type_as)  # Gaussian processes variance
        self.R = _identity(self.nx, self.D, self.type_as)  # rotation in rigid transformation
        self.nonrigid_flag = False  # indicate if to start nonrigid
        self.Dim = _data(self.nx, self.D, self.type_as)
        self.samples_s = self.nx.maximum(
            _prod(self.nx)(self.nx.max(self.coordsA, axis=0) - self.nx.min(self.coordsA, axis=0)),
            _prod(self.nx)(self.nx.max(self.coordsB, axis=0) - self.nx.min(self.coordsB, axis=0)),
        )
        self.outlier_s = self.samples_s * self.NA

        # initialize the SVI
        if self.SVI_mode:
            self.SVI_deacy = _data(self.nx, 10.0, self.type_as)
            # Select a random subset of data
            self.batch_size = min(max(int(self.NB / 10), self.batch_size), self.NB)
            self.batch_perm = _randperm(self.nx)(self.NB)
            self.Sp, self.Sp_spatial, self.Sp_sigma2 = 0, 0, 0
            self.SigmaInv = self.nx.zeros((self.K, self.K), type_as=self.type_as)  # K x K
            self.PXB_term = self.nx.zeros((self.NA, self.D), type_as=self.type_as)  # NA x D

        if self.use_chunk:
            chunk_base = 1e8  # 1e7
            self.split_size = min(int(self.chunk_capacity * chunk_base / (self.NA)), self.NB)
            self.split_size = 1 if self.split_size == 0 else self.split_size

    def _init_probability_parameters(
        self,
        subsample: int = 20000,
    ):
        for i, (exp_A, exp_B, d_s, p_t, p_p) in enumerate(
            zip(
                self.exp_layers_A,
                self.exp_layers_B,
                self.dissimilarity,
                self.probability_type,
                self.probability_parameters,
            )
        ):

            if p_p is not None:
                continue
            if p_t.lower() == "gauss":
                sub_sample_A = (
                    np.random.choice(self.NA, subsample, replace=False) if self.NA > subsample else np.arange(self.NA)
                )
                sub_sample_B = (
                    np.random.choice(self.NB, subsample, replace=False) if self.NB > subsample else np.arange(self.NB)
                )
                [exp_dist] = calc_distance(
                    X=exp_A[sub_sample_A, :],
                    Y=exp_B[sub_sample_B, :],
                    metric=d_s,
                )
                min_exp_dist = self.nx.min(exp_dist, 1)
                self.probability_parameters[i] = (
                    min_exp_dist[self.nx.argsort(min_exp_dist)[int(sub_sample_A.shape[0] * 0.05)]] / 5
                )
            else:
                pass

    def _construct_kernel(
        self,
        inducing_variables_num,
        sampling_method,
    ):
        unique_spatial_coords = _unique(self.nx, self.coordsA, 0)
        inducing_variables_idx = np.random.choice(unique_spatial_coords.shape[0], inducing_variables_num, replace=False)
        self.inducing_variables = unique_spatial_coords[inducing_variables_idx, :]
        # (self.inducing_variables, _) = sample(
        #     X=unique_spatial_coords, n_sampling=inducing_variables_num, sampling_method=sampling_method
        # )
        if self.kernel_type == "euc":
            self.GammaSparse = con_K(X=self.inducing_variables, Y=self.inducing_variables, beta=self.kernel_bandwidth)
            self.U = con_K(X=self.coordsA, Y=self.inducing_variables, beta=self.kernel_bandwidth)
            self.U_I = (
                con_K(X=self.X_AI, Y=self.inducing_variables, beta=self.kernel_bandwidth) if self.guidance else None
            )
        else:
            raise NotImplementedError(f"Kernel type '{self.kernel_type}' is not implemented.")

        self.K = self.inducing_variables.shape[0]

    def _update_batch(
        self,
        iter,
    ):
        self.step_size = self.nx.minimum(_data(self.nx, 1.0, self.type_as), self.SVI_deacy / (iter + 1.0))
        self.batch_idx = self.batch_perm[: self.batch_size]
        self.batch_perm = _roll(self.nx)(self.batch_perm, self.batch_size)  # move the batch_perm

    def _coarse_rigid_alignment(
        self,
        n_sampling=20000,
        top_K=10,
    ):
        if self.verbose:
            lm.main_info(message="Performing coarse rigid alignment...", indent_level=1)

        # TODO: downsampling here
        sampling_idxA = (
            np.random.choice(self.NA, n_sampling, replace=False) if self.NA > n_sampling else np.arange(self.NA)
        )
        sampling_idxB = (
            np.random.choice(self.NB, n_sampling, replace=False) if self.NB > n_sampling else np.arange(self.NB)
        )
        sampleA = self.sampleA[sampling_idxA]
        sampleB = self.sampleB[sampling_idxB]
        coordsA = self.coordsA[sampling_idxA, :]
        coordsB = self.coordsB[sampling_idxB, :]
        N, M, D = coordsA.shape[0], coordsB.shape[0], coordsA.shape[1]

        X_A = get_rep(
            nx=self.nx,
            type_as=self.type_as,
            sample=sampleA,
            rep=self.init_layer,
            rep_field=self.init_field,
            genes=self.genes,
        )
        X_B = get_rep(
            nx=self.nx,
            type_as=self.type_as,
            sample=sampleB,
            rep=self.init_layer,
            rep_field=self.init_field,
            genes=self.genes,
        )

        # voxeling the data
        coordsA, X_A = voxel_data(
            nx=self.nx,
            coords=coordsA,
            gene_exp=X_A,
            voxel_num=max(min(int(N / 20), 1000), 100),
        )
        coordsB, X_B = voxel_data(
            nx=self.nx,
            coords=coordsB,
            gene_exp=X_B,
            voxel_num=max(min(int(M / 20), 1000), 100),
        )

        # calculate the similarity distance purely based on expression
        [exp_dist] = calc_distance(
            X=X_A,
            Y=X_B,
            metric="kl" if self.init_field == "layer" else "euc",
        )

        # construct matching pairs based on brute force mutual K-NN. Here we use numpy backend
        # TODO: we can use GPU to search KNN and then convert to CPU
        item2 = np.argpartition(exp_dist, top_K, axis=0)[:top_K, :].T
        item1 = np.repeat(np.arange(exp_dist.shape[1])[:, None], top_K, axis=1)
        NN1 = np.dstack((item1, item2)).reshape((-1, 2))
        distance1 = exp_dist.T[NN1[:, 0], NN1[:, 1]]

        item1 = np.argpartition(exp_dist, top_K, axis=1)[:, :top_K]
        item2 = np.repeat(np.arange(exp_dist.shape[0])[:, None], top_K, axis=1)
        NN2 = np.dstack((item1, item2)).reshape((-1, 2))
        distance2 = exp_dist.T[NN2[:, 0], NN2[:, 1]]

        NN = np.vstack((NN1, NN2))
        distance = np.r_[distance1, distance2]

        # input pairs
        train_x, train_y = coordsA[NN[:, 1], :], coordsB[NN[:, 0], :]

        # coarse alignment core function
        P, R, t, init_weight, sigma2, gamma = inlier_from_NN(train_x, train_y, distance[:, None])

        # if allow_filp, then try to flip the data
        if self.allow_flip:
            R_flip = np.eye(D)
            R_flip[-1, -1] = -1
            P2, R2, t2, init_weight, sigma2_2, gamma_2 = inlier_from_NN(
                np.dot(train_x, R_flip), train_y, distance[:, None]
            )
            if gamma_2 > gamma:
                P = P2
                R = R2
                t = t2
                sigma2 = sigma2_2
                R = np.dot(R, R_flip)
                lm.main_info(message="Flipping detected in coarse rigid alignment.", indent_level=2)
        inlier_threshold = min(P[np.argsort(-P[:, 0])[20], 0], 0.5)
        inlier_set = np.where(P[:, 0] > inlier_threshold)[0]
        inlier_x, inlier_y = train_x[inlier_set, :], train_y[inlier_set, :]
        inlier_P = P[inlier_set, :]

        # convert to correct data type
        self.inlier_A = self.nx.from_numpy(inlier_x, type_as=self.type_as)
        self.inlier_B = self.nx.from_numpy(inlier_y, type_as=self.type_as)
        self.inlier_P = self.nx.from_numpy(inlier_P, type_as=self.type_as)
        self.init_R = self.nx.from_numpy(R, type_as=self.type_as)
        self.init_t = self.nx.from_numpy(t, type_as=self.type_as)

        if self.verbose:
            lm.main_info(message="Coarse rigid alignment done.", indent_level=1)

    def _save_iter(self, iter):
        self.iter_added[self.key_added][iter] = (
            self.nx.to_numpy(self.XAHat * self.normalize_scales[1] + self.normalize_means[1])
            if self.normalize_c
            else self.nx.to_numpy(self.XAHat)
        )
        self.iter_added["sigma2"][iter] = self.nx.to_numpy(self.sigma2)
        # self.iter_added["beta2"][iter] = nx.to_numpy(beta2)

    def _update_assignment_P(
        self,
    ):
        model_mul = _unsqueeze(self.nx)(self.alpha * self.nx.exp(-self.SigmaDiag / self.sigma2), -1)  # N x 1
        common_kwargs = dict(
            nx=self.nx,
            type_as=self.type_as,
            Dim=self.Dim,
            sigma2=self.sigma2,
            model_mul=model_mul,
            gamma=self.gamma,
            samples_s=self.samples_s,
            sigma2_variance=self.sigma2_variance,
            probability_type=self.probability_type,
            probability_parameters=self.probability_parameters,
            sparse_calculation_mode=self.sparse_calculation_mode,
            top_k=self.sparse_top_k,
        )

        if self.SVI_mode:
            if self.use_chunk:
                spatial_XB_chunks = _split(self.nx, self.coordsB, self.split_size, dim=0)
                exp_layer_B_chunks = _split(self.nx, self.exp_layers_B, self.split_size, dim=0)
                # initial results for chunk
                K_NA_spatial = self.nx.zeros((self.NA,), type_as=self.type_as)
                K_NA_sigma2 = self.nx.zeros((self.NA,), type_as=self.type_as)

                Ps = []
                sigma2_related = 0

                for spatial_XB_chunk, exp_layer_B_chunk in zip(spatial_XB_chunks, exp_layer_B_chunks):
                    # calculate the spatial distance
                    [spatial_dist] = calc_distance(self.XAHat, spatial_XB_chunk, metric="euc")

                    # calculate the expression / representation distances
                    exp_layer_dist = calc_distance(
                        self.exp_layers_A, exp_layer_B_chunk, self.dissimilarity, self.label_transfer
                    )

                    P, K_NA_spatial_chunk, K_NA_sigma2_chunk, sigma2_related_chunk = get_P_core(
                        spatial_dist=spatial_dist, exp_dist=exp_layer_dist, **common_kwargs
                    )

                    # add / update chunk results
                    Ps.append(P)
                    K_NA_spatial += K_NA_spatial_chunk
                    K_NA_sigma2 += K_NA_sigma2_chunk
                    sigma2_related += sigma2_related_chunk

                # concatenate / process chunk results
                # P = _concat(self.nx, Ps, axis=1, sparse=self.sparse_calculation_mode)
                self.P = self.nx.concatenate(Ps, axis=1)
                self.K_NA_sigma2 = K_NA_sigma2
                self.Sp_sigma2 = K_NA_sigma2.sum()
                self.K_NA_spatial = K_NA_spatial

            else:
                [spatial_dist] = calc_distance(
                    X=self.XAHat,
                    Y=self.coordsB[self.batch_idx, :],
                    metric="euc",
                )  # NA x batch_size (SVI_mode) / NA x NB (not SVI_mode)
                if self.pre_compute_dist:
                    exp_layer_dist = [exp_layer_d[:, self.batch_idx] for exp_layer_d in self.exp_layer_dist]
                else:
                    exp_layer_dist = calc_distance(
                        X=self.exp_layer_A,
                        Y=[e_l[self.batch_idx] for e_l in self.exp_layer_B],
                        metric=self.dissimilarity,
                        label_transfer=self.label_transfer,
                    )  # NA x batch_size (SVI_mode) / NA x NB (not SVI_mode)

                self.P, self.K_NA_spatial, self.K_NA_sigma2, self.sigma2_related = get_P_core(
                    spatial_dist=spatial_dist, exp_dist=exp_layer_dist, **common_kwargs
                )
        else:
            [spatial_dist] = calc_distance(
                X=self.XAHat,
                Y=self.coordsB,
                metric="euc",
            )  # NA x batch_size (SVI_mode) / NA x NB (not SVI_mode)
            self.P, self.K_NA_spatial, self.K_NA_sigma2, self.sigma2_related = get_P_core(
                spatial_dist=spatial_dist, exp_dist=self.exp_layer_dist, **common_kwargs
            )
        self.Sp = self.P.sum()
        self.Sp_sigma2 = self.K_NA_sigma2.sum()
        self.Sp_spatial = self.K_NA_spatial.sum()
        self.sigma2_related = self.sigma2_related / (self.Dim * self.Sp_sigma2)
        self.K_NA = self.nx.sum(self.P, axis=1)
        self.K_NB = self.nx.sum(self.P, axis=0)

    def _update_gamma(
        self,
    ):
        if self.SVI_mode:
            self.gamma = self.nx.exp(
                _psi(self.nx)(self.gamma_a + self.Sp_spatial)
                - _psi(self.nx)(self.gamma_a + self.gamma_b + self.batch_size)
            )
        else:
            self.gamma = self.nx.exp(
                _psi(self.nx)(self.gamma_a + self.Sp_spatial) - _psi(self.nx)(self.gamma_a + self.gamma_b + self.NB)
            )
        self.gamma = _data(self.nx, 0.99, self.type_as) if self.gamma > 0.99 else self.gamma
        self.gamma = _data(self.nx, 0.01, self.type_as) if self.gamma < 0.01 else self.gamma

    def _update_alpha(
        self,
    ):
        if self.SVI_mode:
            # Using SVI mode for alpha update
            self.alpha = (
                self.step_size
                * self.nx.exp(
                    _psi(self.nx)(self.kappa + self.K_NA_spatial)
                    - _psi(self.nx)(self.kappa * self.NA + self.Sp_spatial)
                )
                + (1 - self.step_size) * self.alpha
            )
        else:
            # Full update for alpha
            self.alpha = self.nx.exp(
                _psi(self.nx)(self.kappa + self.K_NA_spatial) - _psi(self.nx)(self.kappa * self.NA + self.Sp_spatial)
            )

    def _update_nonrigid(
        self,
    ):
        SigmaInv = self.sigma2 * self.lambdaVF * self.GammaSparse + _dot(self.nx)(
            self.U.T, self.nx.einsum("ij,i->ij", self.U, self.K_NA)
        )

        if self.SVI_mode:
            PXB_term = _dot(self.nx)(self.P, self.coordsB[self.batch_idx, :]) - self.nx.einsum(
                "ij,i->ij", self.RnA, self.K_NA
            )
            self.SigmaInv = self.step_size * SigmaInv + (1 - self.step_size) * self.SigmaInv
            self.PXB_term = self.step_size * PXB_term + (1 - self.step_size) * self.PXB_term
        else:
            self.PXB_term = _dot(self.nx)(self.P, self.coordsB) - self.nx.einsum("ij,i->ij", self.RnA, self.K_NA)
            self.SigmaInv, self.PXB_term = SigmaInv, PXB_term

        UPXB_term = _dot(self.nx)(self.U.T, self.PXB_term)

        # TODO: can we store these kernel multiple results? They are fixed
        if self.guidance and ((self.guidance_effect == "nonrigid") or (self.guidance_effect == "both")):
            self.SigmaInv += (self.sigma2 / self.guidance_epsilon) * _dot(self.nx)(self.U_I.T, self.U_I)
            self.UPXB_term += (self.sigma2 / self.guidance_epsilon) * _dot(self.nx)(self.U_I.T, self.X_BI - self.R_AI)

        Sigma = _pinv(self.nx)(self.SigmaInv)
        self.Coff = _dot(self.nx)(Sigma, UPXB_term)

        self.VnA = _dot(self.nx)(self.U, self.Coff)
        if self.guidance and ((self.guidance_effect == "nonrigid") or (self.guidance_effect == "both")):
            self.V_AI = _dot(self.nx)(self.U_I, self.Coff)
        self.SigmaDiag = self.sigma2 * self.nx.einsum(
            "ij->i", self.nx.einsum("ij,ji->ij", self.U, _dot(self.nx)(Sigma, self.U.T))
        )

    def _update_rigid(
        self,
    ):

        PXA, PVA, PXB = (
            _dot(self.nx)(self.K_NA, self.coordsA)[None, :],
            _dot(self.nx)(self.K_NA, self.VnA)[None, :],
            _dot(self.nx)(self.K_NB, self.coordsB[self.batch_idx, :])[None, :]
            if self.SVI_mode
            else _dot(self.nx)(self.K_NB, self.coordsB)[None, :],
        )

        # solve rotation using SVD formula
        mu_XB, mu_XA, mu_Vn = PXB, PXA, PVA
        mu_X_deno, mu_Vn_deno = self.Sp, self.Sp
        if self.guidance and (self.guidance_effect in ("rigid", "both")):
            mu_XB += (self.sigma2 / self.guidance_epsilon) * self.X_BI
            mu_XA += (self.sigma2 / self.guidance_epsilon) * self.X_AI
            mu_Vn += (self.sigma2 / self.guidance_epsilon) * self.V_AI
            mu_X_deno += (self.sigma2 / self.guidance_epsilon) * self.X_BI.shape[0]
            mu_Vn_deno += (self.sigma2 / self.guidance_epsilon) * self.X_BI.shape[0]
        if self.nn_init:
            mu_XB += (self.sigma2 / self.lambdaReg) * _dot(self.nx)(self.inlier_P.T, self.inlier_B)
            mu_XA += (self.sigma2 / self.lambdaReg) * _dot(self.nx)(self.inlier_P.T, self.inlier_A)
            mu_X_deno += (self.sigma2 / self.lambdaReg) * self.nx.sum(self.inlier_P)

        mu_XB = mu_XB / mu_X_deno
        mu_XA = mu_XA / mu_X_deno
        mu_Vn = mu_Vn / mu_Vn_deno

        XA_hat = self.coordsA - mu_XA
        VnA_hat = self.VnA - mu_Vn
        XB_hat = (self.coordsB[self.batch_idx, :] - mu_XB) if self.SVI_mode else (self.coordsB - mu_XB)

        if self.guidance and (self.guidance_effect in ("rigid", "both")):
            X_AI_hat = self.X_AI - mu_XA
            X_BI_hat = self.X_BI - mu_XB
            V_AI_hat = self.V_AI - mu_Vn

        if self.nn_init:
            inlier_A_hat = self.inlier_A - mu_XA
            inlier_B_hat = self.inlier_B - mu_XB

        A = -(
            _dot(self.nx)(XA_hat.T, self.nx.einsum("ij,i->ij", VnA_hat, self.K_NA))
            - _dot(self.nx)(_dot(self.nx)(XA_hat.T, self.P), XB_hat)
        ).T

        if self.guidance_effect in ("rigid", "both"):
            A -= (self.sigma2 / self.guidance_epsilon) * _dot(self.nx)(X_AI_hat.T, V_AI_hat - X_BI_hat).T

        if self.nn_init:
            A -= (self.sigma2 / self.lambdaReg) * _dot(self.nx)((inlier_A_hat * self.inlier_P).T, -inlier_B_hat).T

        svdU, svdS, svdV = _linalg(self.nx).svd(A)
        C = _identity(self.nx, self.D, self.type_as)
        C[-1, -1] = _linalg(self.nx).det(_dot(self.nx)(svdU, svdV))

        R = _dot(self.nx)(_dot(self.nx)(svdU, C), svdV)
        if self.SVI_mode and self.step_size < 1:
            self.R = self.step_size * R + (1 - self.step_size) * self.R
        else:
            self.R = R

        # solve translation using SVD formula
        t_numerator = PXB - PVA - _dot(self.nx)(PXA, self.R.T)
        t_deno = self.Sp

        if self.guidance and (self.guidance_effect in ("rigid", "both")):
            t_numerator += (self.sigma2 / self.guidance_epsilon) * self.nx.sum(
                self.X_BI - self.V_AI - _dot(self.nx)(self.X_AI, self.R.T), axis=0
            )
            t_deno += (self.sigma2 / self.guidance_epsilon) * self.X_BI.shape[0]

        if self.nn_init:
            t_numerator += (self.sigma2 / self.lambdaReg) * _dot(self.nx)(
                self.inlier_P.T, self.inlier_B - _dot(self.nx)(self.inlier_A, self.R.T)
            )
            t_deno += (self.sigma2 / self.lambdaReg) * self.nx.sum(self.inlier_P)

        t = t_numerator / t_deno
        if self.SVI_mode and self.step_size < 1:
            self.t = self.step_size * t + (1 - self.step_size) * self.t
        else:
            self.t = t

        self.RnA = _dot(self.nx)(self.coordsA, self.R.T) + self.t
        if self.nn_init:
            self.inlier_R = _dot(self.nx)(self.inlier_A, self.R.T) + self.t
        if self.guidance:
            self.R_AI = _dot(self.nx)(self.R_AI, self.R.T) + self.t

    def _update_sigma2(self, iter):
        self.sigma2 = self.nx.maximum(
            (self.sigma2_related + self.nx.einsum("i,i", self.K_NA_sigma2, self.SigmaDiag) / self.Sp_sigma2),
            _data(self.nx, 1e-3, self.type_as),
        )
        if iter < 100:
            self.sigma2 = self.nx.maximum(self.sigma2, _data(self.nx, 1e-2, self.type_as))

    def _get_optimal_R(
        self,
    ):
        mu_XnA, mu_XnB = (
            _dot(self.nx)(self.K_NA, self.coordsA) / self.Sp,
            _dot(self.nx)(self.K_NB, self.coordsB) / self.Sp,
        )
        XnABar, XnBBar = self.coordsA - mu_XnA, self.coordsB - mu_XnB
        A = _dot(self.nx)(_dot(self.nx)(self.P, XnBBar).T, XnABar)

        # get the optimal rotation matrix R
        svdU, svdS, svdV = _linalg(self.nx).svd(A)
        # TODO: C can be initial once and only (-1,-1) value will be changed
        C = _identity(self.nx, self.D, type_as=self.type_as)
        C[-1, -1] = _linalg(self.nx).det(_dot(self.nx)(svdU, svdV))
        self.R = _dot(self.nx)(_dot(self.nx)(svdU, C), svdV)
        self.t = mu_XnB - _dot(self.nx)(mu_XnA, self.R.T)
        self.optimal_RnA = _dot(self.nx)(self.coordsA, self.R.T) + self.t

    def _wrap_output(
        self,
    ):
        # denormalize
        if self.normalize_c:
            self.XAHat = self.XAHat * self.normalize_scales[1] + self.normalize_means[1]
            self.RnA = self.RnA * self.normalize_scales[1] + self.normalize_means[1]
            self.optimal_RnA = self.optimal_RnA * self.normalize_scales[1] + self.normalize_means[1]

        # Save aligned coordinates
        self.XAHat = self.nx.to_numpy(self.XAHat).copy()
        self.optimal_RnA = self.nx.to_numpy(self.optimal_RnA).copy()
        self.RnA = self.nx.to_numpy(self.RnA).copy()

        if not (self.vecfld_key_added is None):

            self.vecfld = {
                "R": self.nx.to_numpy(self.R),
                "t": self.nx.to_numpy(self.t),
                "optimal_R": self.nx.to_numpy(self.optimal_R),
                "optimal_t": self.nx.to_numpy(self.optimal_t),
                "init_R": self.init_R,
                "init_t": self.init_t,
                "beta": self.beta,
                "Coff": self.nx.to_numpy(self.Coff),
                "inducing_variables": self.nx.to_numpy(self.inducing_variables),
                "normalize_scales": self.nx.to_numpy(self.normalize_scales) if self.normalize_c else None,
                "normalize_means": self.nx.to_numpy(self.normalize_means) if self.normalize_c else None,
                "normalize_c": self.normalize_c,
                "dissimilarity": self.dissimilarity,
                # "beta2": self.nx.to_numpy(self.beta2),
                "sigma2": self.nx.to_numpy(self.sigma2),
                "gamma": self.nx.to_numpy(self.gamma),
                "NA": self.NA,
                "sigma2_variance": self.nx.to_numpy(self.sigma2_variance),
                "method": "Spateo",
            }
