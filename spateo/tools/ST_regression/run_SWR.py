"""
Enables STGWR to be run using the "run" command rather than needing to navigate to and call the main file
(SWR_mpi.py).
"""
import os
import sys

import click

# For now, add Spateo working directory to sys path so compiler doesn't look in the installed packages:
sys.path.insert(0, "/mnt/c/Users/danie/Desktop/Github/Github/spateo-release-main")
import spateo.tools.ST_regression as fast_stgwr


@click.group()
@click.version_option("0.3.2")
def main():
    pass


@main.command()
@click.option(
    "np",
    default=2,
    help="Number of processes to use. Note the max number of processes is " "determined by the number of CPUs.",
    required=True,
)
@click.option("adata_path")
@click.option("coords_key", default="spatial")
@click.option(
    "group_key",
    default="cell_type",
    help="Key to entry in .obs containing cell type "
    "or other category labels. Required if "
    "'mod_type' is 'niche' or 'slice'.",
)
@click.option(
    "group_subset",
    required=False,
    multiple=True,
    help="If provided, only cells with labels that correspond to these group(s) will be used as prediction targets.",
)
@click.option(
    "csv_path",
    required=False,
    help="Can be used to provide a .csv file, containing gene expression data or any other kind of data. "
    "Assumes the first three columns contain x- and y-coordinates and then dependent variable "
    "values, in that order.",
)
@click.option(
    "subsample",
    default=False,
    is_flag=True,
    help="Recommended for large datasets (>5000 samples), " "otherwise model fitting is quite slow.",
)
@click.option("multiscale", default=False, is_flag=True)
@click.option(
    "mod_type",
    default="niche",
    help="If adata_path is provided, one of the STGWR models " "will be used. Options: 'niche', 'lr', 'slice'.",
)
@click.option("grn", default=False, is_flag=True)
@click.option("cci_dir", required=True)
@click.option("species", default="human")
@click.option(
    "output_path",
    default="./output/stgwr_results.csv",
    help="Path to output file. Make sure the parent " "directory is empty- any existing files will " "be deleted.",
)
@click.option("custom_lig_path", required=False)
@click.option("ligand", required=False, multiple=True)
@click.option("custom_rec_path", required=False)
@click.option("receptor", required=False, multiple=True)
@click.option("custom_pathways_path", required=False)
@click.option("pathway", required=False, multiple=True)
@click.option(
    "custom_regulators_path",
    required=False,
    help="Only used for GRN models. This file contains a list "
    "of TFs (or other regulatory molecules)"
    "to constitute the independent variable block.",
)
@click.option(
    "tf",
    required=False,
    multiple=True,
    help="Only used for GRN models. Each input specified using this "
    "is a TF (or other regulatory molecule) to constitute the "
    "independent variable block.",
)
@click.option("custom_targets_path", required=False)
@click.option("target", required=False, multiple=True)
@click.option(
    "target_expr_threshold",
    default=0.2,
    help="For automated selection, the threshold "
    "proportion of cells for which transcript "
    "needs to be expressed in to be selected as a target of interest. "
    "Not used if 'targets_path' is not None.",
)
@click.option(
    "multicollinear_threshold",
    required=False,
    help="Used only if `mod_type` is 'slice'. If this argument is provided, independent variables that are highly "
    "correlated will be filtered out based on variance inflation factor threshold. A value of 5 or 10 is "
    "recommended. This can be useful in reducing computation time.",
)
@click.option("init_betas_path", required=False)
@click.option("normalize", default=False, is_flag=True)
@click.option("smooth", default=False, is_flag=True)
@click.option("log_transform", default=False, is_flag=True)
@click.option(
    "covariate_keys",
    required=False,
    multiple=True,
    help="Any number of keys to entry in .obs or "
    ".var_names of an "
    "AnnData object. Values here will be added to"
    "the model as covariates.",
)
@click.option("bw", required=False)
@click.option("minbw", required=False)
@click.option("maxbw", required=False)
@click.option(
    "bw_fixed",
    default=False,
    is_flag=True,
    help="If this argument is provided, the bandwidth will be "
    "interpreted as a distance during kernel operations. If not, it will be interpreted "
    "as the number of nearest neighbors.",
)
@click.option(
    "exclude_self",
    default=False,
    is_flag=True,
    help="When computing spatial weights, do not count the "
    "cell itself as a neighbor. Recommended to set to "
    "True for the CCI models because the independent "
    "variable array is also spatially-dependent.",
)
@click.option("kernel", default="bisquare")
@click.option("distr", default="gaussian")
@click.option("fit_intercept", default=False, is_flag=True)
@click.option("tolerance", default=1e-3)
@click.option("max_iter", default=1000)
@click.option(
    "patience",
    default=5,
    help="Number of iterations to wait before stopping if parameters have "
    "stabilized. Only used if `multiscale` is True.",
)
@click.option("alpha", required=False)
def run(
    np,
    adata_path,
    coords_key,
    group_key,
    group_subset,
    csv_path,
    multiscale,
    multiscale_params_only,
    mod_type,
    grn,
    cci_dir,
    species,
    output_path,
    custom_lig_path,
    ligand,
    custom_rec_path,
    receptor,
    custom_pathways_path,
    pathway,
    custom_regulators_path,
    tf,
    custom_targets_path,
    target,
    target_expr_threshold,
    init_betas_path,
    normalize,
    smooth,
    log_transform,
    covariate_keys,
    bw,
    minbw,
    maxbw,
    bw_fixed,
    exclude_self,
    kernel,
    distr,
    fit_intercept,
    tolerance,
    max_iter,
    patience,
    alpha,
    chunks,
):
    """Command line shortcut to run any STGWR models.

    Args:
        n_processes: Number of processes to use. Note the max number of processes is determined by the number of CPUs.
        adata_path: Path to AnnData object containing gene expression data
        coords_key: Key to entry in .obs containing x- and y-coordinates
        group_key: Key to entry in .obs containing cell type or other category labels. Required if 'mod_type' is
            'niche' or 'slice'.
        csv_path: Can be used to provide a .csv file, containing gene expression data or any other kind of data.
            Assumes the first three columns contain x- and y-coordinates and then dependent variable values,
            in that order.
        multiscale: If True, the MGWR model will be used
        multiscale_params_only: If True, will only fit parameters for MGWR model and no other metrics. Otherwise,
            the effective number of parameters and leverages will be returned.
        mod_type: If adata_path is provided, one of the SWR models will be used. Options: 'niche', 'lr', 'ligand'.
        grn: If True, the GRN model will be used


        cci_dir: Path to directory containing CCI files
        species: Species for which CCI files were generated. Options: 'human', 'mouse'.
        output_path: Path to output file
        custom_lig_path: Path to file containing a list of ligands to be used in the GRN model
        ligand: Can be used as an alternative to `custom_lig_path`. Can be used to provide a custom list of ligands.
        custom_rec_path: Path to file containing a list of receptors to be used in the GRN model
        receptor: Can be used as an alternative to `custom_rec_path`. Can be used to provide a custom list of
            receptors.
        custom_pathways_path: Rather than providing a list of receptors, can provide a list of signaling pathways-
            all receptors with annotations in this pathway will be included in the model. Only used if :attr `mod_type`
            is "lr".
        custom_regulators_path: Only used for GRN models. This file contains a list of TFs (or other regulatory
            molecules) to constitute the independent variable block.
        tf: Can be used as an alternative to `custom_regulators_path`. Can be used to provide a custom list of
            regulatory factors.
        custom_targets_path: Path to file containing a list of targets to be used in the GRN model
        target: Can be used as an alternative to `custom_targets_path`. Can be used to provide a custom list of
            targets.
        target_expr_threshold: For automated selection, the threshold proportion of cells for which transcript needs
            to be expressed in to be selected as a target of interest.


        init_betas_path: Path to file containing initial values for beta coefficients
        normalize: If True, the data will be normalized
        smooth: If True, the data will be smoothed
        log_transform: If True, the data will be log-transformed
        covariate_keys: Any number of keys to entry in .obs or .var_names of an AnnData object. Values here will
            be added to the model as covariates.
        bw: Bandwidth to use for spatial weights
        minbw: Minimum bandwidth to use for spatial weights
        maxbw: Maximum bandwidth to use for spatial weights
        bw_fixed: If this argument is provided, the bandwidth will be interpreted as a distance during kernel
            operations. If not, it will be interpreted as the number of nearest neighbors.
        exclude_self: When computing spatial weights, do not count the cell itself as a neighbor. Recommended to
            set to True for the CCI models because the independent variable array is also spatially-dependent.
        kernel: Kernel to use for spatial weights. Options: 'bisquare', 'quadratic', 'gaussian', 'triangular',
            'uniform', 'exponential'.
        distr: Distribution to use for spatial weights. Options: 'gaussian', 'poisson', 'nb'.
        fit_intercept: If True, will include intercept in model
        tolerance: Tolerance for convergence of model
        max_iter: Maximum number of iterations for model
        patience: Number of iterations to wait before stopping if parameters have stabilized. Only used if
            `multiscale` is True.
        alpha: Alpha value to use for MGWR model
        chunks: Number of chunks for multiscale computation (default: 1). Increase the number if run out of memory
            but should keep it as low as possible.
    """

    mpi_path = os.path.dirname(fast_stgwr.__file__) + "/SWR_mpi.py"

    command = (
        "mpiexec "
        + " -np "
        + str(np)
        + " python "
        + mpi_path
        + " -mod_type "
        + mod_type
        + " -species "
        + species
        + " -output_path "
        + output_path
        + " -target_expr_threshold "
        + str(target_expr_threshold)
        + " -coords_key "
        + coords_key
        + " -group_key "
        + group_key
        + " -kernel "
        + kernel
        + " -distr "
        + distr
        + " -tolerance "
        + str(tolerance)
        + " -max_iter "
        + str(max_iter)
        + " -patience "
        + str(patience)
    )

    if adata_path is not None:
        command += " -adata_path " + adata_path
    elif csv_path is not None:
        command += " -csv_path " + csv_path

    if group_subset is not None:
        command += " -group_subset "
        for key in group_subset:
            command += key + " "

    if multiscale:
        command += " -multiscale "
    if multiscale_params_only:
        command += " -multiscale_params_only "
    if grn:
        command += " -grn "
    if cci_dir is not None:
        command += " -cci_dir " + cci_dir

    if custom_lig_path is not None:
        command += " -custom_lig_path " + custom_lig_path
    if ligand is not None:
        command += " -ligand "
        for lig in ligand:
            command += lig + " "

    if custom_rec_path is not None:
        command += " -custom_rec_path " + custom_rec_path
    if receptor is not None:
        command += " -receptor "
        for rec in receptor:
            command += rec + " "

    if custom_pathways_path is not None:
        command += " -custom_pathways_path " + custom_pathways_path
    if pathway is not None:
        command += " -pathway "
        for path in pathway:
            command += path + " "

    if custom_regulators_path is not None:
        command += " -custom_regulators_path " + custom_regulators_path
    if tf is not None:
        command += " -tf "
        for t in tf:
            command += t + " "

    if custom_targets_path is not None:
        command += " -custom_targets_path " + custom_targets_path
    if target is not None:
        command += " -target "
        for tar in target:
            command += tar + " "

    if init_betas_path is not None:
        command += " -init_betas_path " + init_betas_path
    if normalize:
        command += " -normalize "
    if smooth:
        command += " -smooth "
    if log_transform:
        command += " -log_transform "
    if covariate_keys is not None:
        command += " -covariate_keys "
        for key in covariate_keys:
            command += key + " "
    if bw is not None:
        command += " -bw " + str(bw)
    if minbw is not None:
        command += " -minbw " + str(minbw)
    if maxbw is not None:
        command += " -maxbw " + str(maxbw)
    if bw_fixed:
        command += " -bw_fixed "
    if exclude_self:
        command += " -exclude_self "
    if fit_intercept:
        command += " -fit_intercept "
    if chunks is not None:
        command += " -chunks " + str(chunks)
    if alpha is not None:
        command += " -alpha " + str(alpha)

    os.system(command)
    pass


if __name__ == "__main__":
    main()

# ADD SOME DEFAULT OPTIONS LATER
