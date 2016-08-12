""" Classes and functions for fitting ivim model """
from __future__ import division, print_function, absolute_import
import numpy as np

from dipy.core.gradients import gradient_table
from dipy.reconst.base import ReconstModel
from dipy.reconst.dti import apparent_diffusion_coef
from dipy.reconst.dti import TensorModel, mean_diffusivity
from dipy.reconst.multi_voxel import multi_voxel_fit
from dipy.reconst.vec_val_sum import vec_val_vect
from dipy.core.sphere import Sphere
from dipy.core.optimize import Optimizer
from distutils.version import LooseVersion
import scipy

SCIPY_LESS_0_17 = LooseVersion(scipy.version.short_version) < '0.17'

if not SCIPY_LESS_0_17:
    least_squares = scipy.optimize.least_squares
else:
    leastsq = scipy.optimize.leastsq


def ivim_prediction(params, gtab, S0=1.):
    """The Intravoxel incoherent motion (IVIM) model function.

    Parameters
    ----------
    params : array
        An array of IVIM parameters - S0, f, D_star, D

    gtab : GradientTable class instance
        Gradient directions and bvalues

    S0 : float, optional
        This has been added just for consistency with the existing
        API. Unlike other models, IVIM predicts S0 and this is over written
        by the S0 value in params.

    References
    ----------
    .. [1] Le Bihan, Denis, et al. "Separation of diffusion
               and perfusion in intravoxel incoherent motion MR
               imaging." Radiology 168.2 (1988): 497-505.
    .. [2] Federau, Christian, et al. "Quantitative measurement
               of brain perfusion with intravoxel incoherent motion
               MR imaging." Radiology 265.3 (2012): 874-881.
    """
    S0, f, D_star, D = params
    b = gtab.bvals
    S = S0 * (f * np.exp(-b * D_star) + (1 - f) * np.exp(-b * D))
    return S


def _ivim_error(params, gtab, signal):
    """Error function to be used in fitting the IVIM model

    Parameters
    ----------
    params : array
        An array of IVIM parameters. [S0, f, D_star, D]

    gtab : GradientTable class instance
        Gradient directions and bvalues.

    signal : array
        Array containing the actual signal values.

    """
    return (signal - ivim_prediction(params, gtab))

def D_star_prediction(D_star, gtab, other_params):
    """Function used to predict D_star when S0, f and D are known

    Parameters
    ----------
    D_star : float
        The value of D_star that needs to be fit

    gtab : GradientTable class instance
        Gradient directions and bvalues.

    other_params : array, dtype=float
        The parameters S0, f and D which are fixed
    """
    S0, f, D = other_params
    b = gtab.bvals
    S = S0 * (f * np.exp(-b * D_star) + (1 - f) * np.exp(-b * D))
    return S

def D_star_error(D_star, gtab, signal, other_params):
    """Error function used to fit D_star kepping S0, f and D fixed
        Parameters
    ----------
    D_star : float
        The value of D_star that needs to be fit

    gtab : GradientTable class instance
        Gradient directions and bvalues.
    
    signal : array
        Array containing the actual signal values.

    other_params : array, dtype=float
        The parameters S0, f and D which are fixed
    """
    S0, f, D = other_params
    return (signal - D_star_prediction(D_star, gtab, other_params))

class IvimModel(ReconstModel):
    """Ivim model
    """

    def __init__(self, gtab, split_b=200.0, method="two_stage",
                 bounds=None, tol=1e-10,
                 options={'gtol': 1e-10, 'ftol': 1e-10,
                          'eps': 1e-10, 'maxiter': 1000}):
        """
        Initialize an IVIM model.

        The IVIM model assumes that biological tissue includes a volume
        fraction 'f' of water flowing in perfused capillaries, with a
        perfusion coefficient D* and a fraction (1-f) of static (diffusion
        only), intra and extracellular water, with a diffusion coefficient
        D. In this model the echo attenuation of a signal in a single voxel
        can be written as

            .. math::

            S(b) = S_0[f*e^{(-b*D\*)} + (1-f)e^{(-b*D)}]

            Where:
            .. math::

            S_0, f, D\* and D are the IVIM parameters.

        Parameters
        ----------
        gtab : GradientTable class instance
            Gradient directions and bvalues

        split_b : float, optional
            The b-value to split the data on for two-stage fit.
            default : 200.

        method : str, optional
            One of either 'two_stage' or 'one_stage'.

            'one_stage' fits using the following method : 
                Linear fitting for D (bvals > 200) and store S0_prime.
                Another linear fit for S0 (bvals < 200).
                Estimate f using 1 - S0_prime/S0.
                Use least squares to fit only D_star. 
            
            'two_stage' performs another fitting using the parameters obtained
            in 'one_stage'. This method gives a roboust fit.

            These two methods were adopted since a straight forward fitting
            gives solutions which are not physical (negative values of f, D_star, D).
            For some regions the solution jumps to either D=0 or D_star=0
            giving unreasonable values for f. In Federau's paper, f values > 0.3
            and D_star values > 0.05 are discarded.

            default : 'two_stage'

        bounds : tuple of arrays with 4 elements, optional
            Bounds to constrain the fitted model parameters. This is only supported for
            Scipy version > 0.17. When using a older scipy version, this function will raise
            an error if bounds are different from None.
            default : ([0., 0., 0., 0.], [np.inf, 1., 1., 1.])

        tol : float, optional
            Tolerance for convergence of minimization.
            default : 1e-7

        options : dict, optional
            Dictionary containing gtol, ftol, eps and maxiter. This is passed
            to leastsq.
            default : options={'gtol': 1e-7, 'ftol': 1e-7, 'eps': 1e-7, 'maxiter': 1000}

        References
        ----------
        .. [1] Le Bihan, Denis, et al. "Separation of diffusion
                   and perfusion in intravoxel incoherent motion MR
                   imaging." Radiology 168.2 (1988): 497-505.
        .. [2] Federau, Christian, et al. "Quantitative measurement
                   of brain perfusion with intravoxel incoherent motion
                   MR imaging." Radiology 265.3 (2012): 874-881.
        """
        if not np.any(gtab.b0s_mask):
            e_s = "No measured signal at bvalue == 0."
            e_s += "The IVIM model requires signal measured at 0 bvalue"
            raise ValueError(e_s)
        ReconstModel.__init__(self, gtab)
        self.split_b = split_b
        self.bounds = bounds
        self.tol = tol
        self.options = options
        self.method = method

        if SCIPY_LESS_0_17 and self.bounds is not None:
            e_s = "Scipy versions less than 0.17 do not support "
            e_s += "bounds. Please update to Scipy 0.17 to use bounds"
            raise ValueError(e_s)
        else:
            self.bounds = (np.array([0., 0., 0., 0.]),
                           np.array([np.inf, 1., 0.05, 0.005]))

    

    @multi_voxel_fit
    def fit(self, data, mask=None):
        """ Fit method of the Ivim model class

        Parameters
        ----------
        data : array
            The measured signal from one voxel. A multi voxel decorator
            will be applied to this fit method to scale it and apply it
            to multiple voxels.

        mask : array
            A boolean array used to mark the coordinates in the data that
            should be analyzed that has the shape data.shape[:-1]

        Returns
        -------
        IvimFit object

        References
        ----------
        .. [1] Federau, Christian, et al. "Quantitative measurement
                   of brain perfusion with intravoxel incoherent motion
                   MR imaging." Radiology 265.3 (2012): 874-881.
        """
        # Call the function estimate_S0_D to get initial x0 guess.
        # x0 = self.estimate_x0(data)
        # Use leastsq to get ivim_params
        S0_prime, D = self.estimate_S0_prime_D(data)
        S0, D_star_prime = self.estimate_S0_D_star_prime(data)
        f = 1 - S0_prime/S0
        D_star = self.estimate_D_star(data, [S0, f, D])
        # Use leastsq to get ivim_params
        x0 = np.array([S0, f, D_star, D])
        if self.method == 'one_stage':
            return IvimFit(self, x0)

        else:
            if self.bounds is None:
                bounds_check = [(0., 0., 0., 0.), (np.inf, 1., 0.01, 0.001)]
            else:
                bounds_check = self.bounds

            x0 = np.where(x0 > bounds_check[0], x0, bounds_check[0])
            x0 = np.where(x0 < bounds_check[1], x0, bounds_check[1])
            
            params_in_mask = self._leastsq(data, x0)
            return IvimFit(self, params_in_mask)


    def estimate_S0_prime_D(self, data):
        """Estimate S0_prime and D for bvals > split_b
        """
        bvals_ge_split = self.gtab.bvals[self.gtab.bvals >= self.split_b]
        bvecs_ge_split = self.gtab.bvecs[self.gtab.bvals >= self.split_b]
        gtab_ge_split = gradient_table(bvals_ge_split, bvecs_ge_split.T)

        D, neg_log_S0 = np.polyfit(gtab_ge_split.bvals,
                                         -np.log(data[self.gtab.bvals >= self.split_b]), 1)
        S0_prime = np.exp(-neg_log_S0)
        return S0_prime, D

    def estimate_S0_D_star_prime(self, data):
        """Estimate S0 and D_star_prime for bvals < 200
        """
        bvals_le_split = self.gtab.bvals[self.gtab.bvals < self.split_b]
        bvecs_le_split = self.gtab.bvecs[self.gtab.bvals < self.split_b]
        gtab_le_split = gradient_table(bvals_le_split, bvecs_le_split.T)

        D_star_prime, neg_log_S0 = np.polyfit(gtab_le_split.bvals,
                                         -np.log(data[self.gtab.bvals < self.split_b]), 1)

        S0 = np.exp(-neg_log_S0)
        return S0, D_star_prime

    def estimate_D_star(self, data, other_params):
        """Estimate D_star using the values of all the other parameters obtained before
        """
        gtol = self.options["gtol"]
        ftol = self.options["ftol"]
        xtol = self.tol
        epsfcn = self.options["eps"]
        maxfev = self.options["maxiter"]

        if SCIPY_LESS_0_17:
            res = leastsq(D_star_error,
                          0.0005,
                          args=(self.gtab, data, other_params),
                          gtol=gtol,
                          xtol=xtol,
                          ftol=ftol,
                          epsfcn=epsfcn,
                          maxfev=maxfev)
            D_star = res[0]
            return D_star
        else:
            res = least_squares(D_star_error,
                                0.0005,
                                ftol=ftol,
                                xtol=xtol,
                                gtol=gtol,
                                bounds= ((0., 0.01)),
                                max_nfev=maxfev,
                                args=(self.gtab, data, other_params))
            D_star = res.x
            return D_star

        params_in_mask = self._leastsq(data, x0)
        return IvimFit(self, params_in_mask)

    def predict(self, ivim_params, gtab, S0=1.):
        """
        Predict a signal for this IvimModel class instance given parameters.

        Parameters
        ----------
        ivim_params : array
            The ivim parameters as an array [S0, f, D_star and D]

        Returns
        -------
        ivim_signal : array
            The predicted IVIM signal using given parameters.
        """
        return ivim_prediction(ivim_params, gtab)

    def _leastsq(self, data, x0):
        """
        Use leastsq for finding ivim_params

        Parameters
        ----------
        data : array, (len(bvals))
            An array containing the signal from a voxel.
            If the data was a 3D image of 10x10x10 grid with 21 bvalues,
            the multi_voxel decorator will run the single voxel fitting
            on all the 1000 voxels to get the parameters in
            IvimFit.model_paramters. The shape of the parameter array
            will be (data[:-1], 4).

        x0 : array
            Initial guesses for the parameters S0, f, D_star and D
            calculated using the function `estimate_x0`
        """
        gtol = self.options["gtol"]
        ftol = self.options["ftol"]
        xtol = self.tol
        epsfcn = self.options["eps"]
        maxfev = self.options["maxiter"]
        bounds = self.bounds

        if SCIPY_LESS_0_17:
            res = leastsq(_ivim_error,
                          x0,
                          args=(self.gtab, data),
                          gtol=gtol,
                          xtol=xtol,
                          ftol=ftol,
                          epsfcn=epsfcn,
                          maxfev=maxfev)
            ivim_params = res[0]
            return ivim_params
        else:
            res = least_squares(_ivim_error,
                                x0,
                                bounds=bounds,
                                ftol=ftol,
                                xtol=xtol,
                                gtol=gtol,
                                max_nfev=maxfev,
                                args=(self.gtab, data))
            ivim_params = res.x
            return ivim_params


class IvimFit(object):

    def __init__(self, model, model_params):
        """ Initialize a IvimFit class instance.
            Parameters
            ----------
            model : Model class
            model_params : array
                The parameters of the model. In this case it is an
                array of ivim parameters. If the fitting is done
                for multi_voxel data, the multi_voxel decorator will
                run the fitting on all the voxels and model_params
                will be an array of the dimensions (data[:-1], 4),
                i.e., there will be 4 parameters for each of the voxels.
        """
        self.model = model
        self.model_params = model_params

    def __getitem__(self, index):
        model_params = self.model_params
        N = model_params.ndim
        if type(index) is not tuple:
            index = (index,)
        elif len(index) >= model_params.ndim:
            raise IndexError("IndexError: invalid index")
        index = index + (slice(None),) * (N - len(index))
        return type(self)(self.model, model_params[index])

    @property
    def S0_predicted(self):
        return self.model_params[..., 0]

    @property
    def perfusion_fraction(self):
        return self.model_params[..., 1]

    @property
    def D_star(self):
        return self.model_params[..., 2]

    @property
    def D(self):
        return self.model_params[..., 3]

    @property
    def shape(self):
        return self.model_params.shape[:-1]

    def predict(self, gtab, S0=1.):
        r"""
        Given a model fit, predict the signal.

        Parameters
        ----------
        gtab : GradientTable class instance
               Gradient directions and bvalues

        S0 : float
            S0 value here is not necessary and will
            not be used to predict the signal. It has
            been added to conform to the structure
            of the predict method in multi_voxel which
            requires a keyword argument S0.

        Returns
        -------
        signal : array
            The signal values predicted for this model using
            its parameters.
        """
        return ivim_prediction(self.model_params, gtab)
