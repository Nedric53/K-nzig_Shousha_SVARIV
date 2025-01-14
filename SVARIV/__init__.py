import numpy as np
from scipy.stats import norm
import matplotlib.pyplot as plt
from pylab import rcParams
import io 

# Based on  https://github.com/MaxLugo/SVARIV/blob/master/SVARIV/__init__.py


# Function to calculate the critical value of the Gaussian distribution for given confidence interval
def norm_critval(confidence=0.95, sq=False):
    '''
    Input:
        confidence = 0.95 => 95% confidence interval is the default
    Ouput:
        rv = critical value of Gaussian ppf distribution given the confidence interval
        sq = False (default). If it is going to be squared, the critval
    '''
    rv = norm.ppf(1 - (1 - confidence) / 2) ** 2 if sq else norm.ppf(1 - (1 - confidence) / 2)
    return rv

# Ordinary Least Squares regression function
def ols(Y, X):
    '''
    Input:
        Y = array or matrix endogenous t as rows and n as columns (n=endog variables)  
        X = array or matrix exogenous in the regressions
    Output:
        rv = dictionary with betas_hat, errors matrix, and Y estimated
    '''
    betas = np.linalg.inv(X.T @ X) @ X.T @ Y
    Y_hat = X @ betas
    errors = Y - Y_hat
    rv = {'betas_hat': betas, 'errors': errors, 'Y_hat': Y_hat}
    return rv

# Function to compute Newey-West heteroskedasticity and autocorrelation consistent (HAC) covariance matrix
def NW_hac_STATA(Vars, lags):
    '''
    Input: 
        Vars = variables matrix (has to be ordered in the same way of the model)
        lags = number of lags (scalar) to be used in the algorithm
    Output:
        corrected sigma by lags
    '''
    Sigma0 = (Vars.T @ Vars) / len(Vars)
    sigma_cov_k = lambda V, k: (V[0:-k].T @ V[0:-k]) / len(V)
    S = Sigma0
    for n in range(lags):
        S += (1 - n / (lags + 1)) * sigma_cov_k(Vars, n) @ sigma_cov_k(Vars, n).T
    return S

# Function to compute the Wald test for a given endogenous variable in the context of OLS
def get_gamma_wald(X, Z, eta, p, n, nvar):
    '''
    Input:
        X = matrix of exogenous variables for ols estimation (np.array) 
        Z = matrix or vector of exog instruments (np.array)
        eta = matrix of errors (np.array)
        p = # of lags (scalar)
        n = # of endog (scalar)
        nvar = number of the endog variable to be used in the test
    output:
        WHat, wald, Gamma_hat
    '''
    Gamma_hat = ((Z.T @ eta) / len(eta)).T
    matagg = np.concatenate((X, eta, Z), axis=1).T
    auxeta = np.zeros((len(eta.T), len(matagg), len(matagg.T)))
    val = np.array([eta[i, np.newaxis].T @ matagg[:, i, np.newaxis].T for i in range(len(matagg.T))])
    val_mean = val.mean(axis=0)
    val_minus_mean = val - val_mean
    for i in range(auxeta.shape[2]):
        auxeta[:, :, i] = val_minus_mean[i, :, :]

    AuxHAC2 = [auxeta[:, :, i].reshape(1, np.multiply(*auxeta[:, :, 0].shape), order='F')
               for i in range(auxeta.shape[2])]
    AuxHAC2 = np.concatenate(AuxHAC2, axis=0)
    AuxHAC3 = NW_hac_STATA(AuxHAC2, 0)

    I = np.eye(len(eta.T))
    V = np.kron(I[0, :], I)
    for i in range(1, len(eta.T)):
        V = np.concatenate((V, np.kron(I[i, :], I[i:, :])), axis=0)

    Q1 = X.T @ X / len(X)
    Q2 = Z.T @ X / len(X)
    m = X.shape[1] - (n * p)

    Shat = np.zeros((len(Q1) * eta.shape[1] + len(V), len(Q1) * eta.shape[1] + len(V.T) + n))
    A = np.kron(np.concatenate((np.zeros((n * p, m)), np.eye(n * p)), axis=1) @ np.linalg.inv(Q1),
                np.eye(n))
    Shat[0:A.shape[0], 0:A.shape[1]] = A
    Shat[A.shape[0]:A.shape[0] + V.shape[0], A.shape[1]:A.shape[1] + V.shape[1]] = V
    B = -np.kron(Q2 @ np.linalg.inv(Q1), np.eye(n))
    Shat[-B.shape[0]:, 0:B.shape[1]] = B
    C = np.eye(B.shape[0])
    Shat[-C.shape[0]:, -C.shape[1]:] = C
    WHataux = Shat @ AuxHAC3 @ Shat.T

    L = len(Q1) * len(C)
    WHat = np.zeros((L, L))
    WHat[:-len(C), :-len(C)] = WHataux[:L - len(C), :L - len(C)]
    WHat[-len(C):, -len(C):] = WHataux[-len(C):, -len(C):]
    WHat[-len(C):, :L - len(C)] = WHataux[-len(C):, :L - len(C)]
    WHat[:-len(C), L - len(C):] = WHat[-len(C):, :L - len(C)].T

    place = -len(C) + nvar - 1
    wald = (len(eta)) * Gamma_hat[nvar - 1, 0] ** 2 / WHat[place, place]
    return WHat, wald, Gamma_hat

# Function to compute the impulse response function (IRF) using a linear Cholesky decomposition
def irf_lineal_cholesky(betas, S, periods=21, normalize=True, cumulative=True, koef=0.1):
    '''
    Input:
        betas = array in the order of the model (np.array) no constant 
                with n rows = number of endog variables, in the paper betas=A
        S = covariance matrix (np.array)
            if S has shape nx1 it will assume that S = Gamma_hat
        normalize=True will normalize the shock to be 1 default=True
        cumulative=True will accumulate the irf response default=True
        periods = scalar with the number of periods to use in the irf default=21
        koef =  koef of impact of shock
    Output:
        irf = matrix of irf (np.array) with all irf for all endog variables
    Notice the shock is always over the first variable. 
    '''
    use_chol = True if S.shape[1] >= 2 else False
    Lags = int(betas.shape[1] / betas.shape[0]) 
    num_endog = int(betas.shape[0]) 
    betas_lags = [betas[:, i * num_endog:(i + 1) * num_endog] for i in range(Lags)]
    irf = np.zeros((periods, betas.shape[0]))
    irf_0 = list(np.linalg.cholesky(S)[:, 0]) if use_chol == True else list(S)
    if normalize:
        irf_0 = koef * (np.array(irf_0) / (np.array(irf_0)[0]))
    irf[0, :] = list(irf_0)
    for t in range(1, periods):
        B = betas_lags[:t]
        i_lags = irf[0:t]
        i_lags = i_lags[-len(B):, :]
        i_lags_list = [i_lags[j, :].reshape(num_endog, 1) for j in range(i_lags.shape[0])]
        i_lags_list.reverse()
        agregg = [z[0] @ z[1] for z in zip(B, i_lags_list)]
        sum_t = np.concatenate(agregg, axis=1).sum(axis=1)
        irf[t, :] = sum_t
    if cumulative:
        irf_cum = irf.cumsum(axis=0)
    rv = irf_cum if cumulative else irf
    return rv

# Function to compute the impulse response function (IRF) for a specific endogenous variable using the MA representation
def irf_gamma(betas, Gamma_hat, periods=21, koef=0.1, wrt=0):
    Lags = int(betas.shape[1] / betas.shape[0]) 
    return MA_representation(betas, Lags, periods) @ Gamma_hat / (Gamma_hat[wrt]) * koef

# Function to compute the MA representation of a VAR model
def MA_representation(betas, p, hori=21):    
    '''
    Input:
        betas = matrix with the betas of the reduced ols estimation with
                n rows = number endog variables and columns = number of p lags * n
                in the paper betas=A.
        p = number of lags
        hori = number of forecast periods to be used in the MA representation 
                in the paper is 20. In python, it is going to be 21 because it is not inclusive
    Output:
        C = list of matrices (0 to k elements) with the MA representation 
            in the sense of
            Y_t = Sum_k=0^inf C_k(A) * e_t-k
            where:
                C(A)_k = Sum_m=1^k C_k-m(A) A_m for k=1,2,...    
    Notes: betas_lag doesn't consider the constant of course. 
            For values m>p => A_m=0.
    '''
    n = len(betas)
    A, C = [betas[:, n * i: n * (i + 1)] for i in range(p)], [np.eye(n)]
    for m in range(1, hori):
        C_m = np.array([a @ c for a, c in zip(A[:m], list(reversed(C)))]).sum(axis=0)
        C.append(C_m)
    return C

# Function to compute G matrices for a given VAR model
def Gmatrices(betas, p, hori=21):
    '''
    Input:
        betas = parameters of the ols reduced estimation (no constant).
                n rows = number endog variables and columns = number of p lags * n
                in the paper betas=A.
        p = number of lags in the var
        horin = number of forecast periods
    Output:
        rv = {'G': gradient matrix for a given horizon,
              'Gcum':G (cumulative)}
    Notes: it is 3D array elements in the dictionary rv. The 2 axis is the horizon
    axis
    '''
    n = len(betas)
    # create the MA representation
    C = MA_representation(betas, p, hori)[1:]
    C_aux = ([np.eye(n)] + C[:hori])[:hori-1]
    J = np.concatenate([np.eye(n), np.zeros((n, (p - 1) * n))], axis=1)
    Alut = np.concatenate([betas, np.zeros((len(betas.T) - len(betas), len(betas.T)))], axis=0)
    Alut[len(betas):, :len(Alut.T) - len(betas)] = np.eye(len(Alut[len(betas):, :]))
    AJ = [np.linalg.matrix_power(Alut, h) @ J.T for h in range(hori - 1)]
    AJp = np.concatenate(AJ, axis=1).T
    AJaux = []
    for i in range(hori - 1):
        l = np.kron(AJp[:n * (hori - i), :], C_aux[i])
        Z = np.zeros((AJp.shape[0] * n, AJp.shape[1] * n))
        Z[i * n**2:, :] = l[:len(Z) - n**2 * i, :]
        AJaux.append(Z)
    G0 = np.array(AJaux).sum(axis=0).T.reshape(p * n**2, n**2, hori - 1, order='F')
    Gaux = np.moveaxis(G0, [0, 1, 2], [1, 0, 2])
    G = np.zeros((Gaux.shape[0], Gaux.shape[1], Gaux.shape[2] + 1))
    for i in range(1, Gaux.shape[2] + 1):
        G[:, :, i] = Gaux[:, :, i - 1]
    Gcum = np.cumsum(G, 2)
    return {'G': G, 'Gcum': Gcum}

# Function to compute confidence intervals using the Delta method
def CI_dmethod(Gamma_hat, WHat, G, T, C, hori=21, confidence=0.95, scale=1, nvar=1):
    '''
    Compute confidence intervals using the D-method.

    Input:
        Gamma_hat = estimate of Gamma according to the paper with n rows= number of endog
                    n columns=1
        WHat = Block covariance matrix = [[W1 , W12], [W12 , W2]] 
        G = Gradient matrix to be used, could be G or Gcum
        T = number of observations
        hori = 21 by default. Periods in the forecast. Same length as G.shape[1]
        C = The Ma representation of A (betas_lag) in a list form 
        confidence = 0.95 by default. Is the confidence value to be used for the critical value
        scale = 1 by default which is the normalization to 1 in the first variable
        nvar = 1 by default which is the endog variable used as the normalization in Gamma_hat

    Output:
        rv = dictionary with the confidence intervals,
           = {'l': MSWlbound, 'u': MSWubound, 'ahat': ahat, 'bhat': bhat, 'chat': chat,
              'Deltahat': Deltahat, 'casedummy': casedummy}        
    '''

    # Extract dimensions
    n = len(Gamma_hat)

    # Calculate critical value
    critval = norm.ppf(1-(1-confidence)/2)**2

    # Extract submatrices from WHat
    W1, W2 = WHat[:-n,:-n], WHat[-n:,-n:]
    W12 = WHat[W1.shape[0]:,:W1.shape[0]].T

    # Identity matrix
    e = np.eye(n)

    # Initialize arrays for storing results
    ahat = np.zeros((n, hori))
    bhat = np.zeros((n, hori))
    chat = np.zeros((n, hori))
    Deltahat = np.zeros((n, hori))
    MSWlbound = np.zeros((n, hori))
    MSWubound = np.zeros((n, hori)) 
    casedummy = np.zeros((n, hori))

    # Loop through endogenous variables
    for j in range(n):    
        # Loop through forecast periods
        for ih in range(hori):
            # Calculate ahat, bhat, chat, and Deltahat
            ahat[j,ih] = T*(Gamma_hat[nvar-1, 0]**2) - critval*W2[nvar-1, nvar-1] 
            bhat[j, ih] = (-2*T*scale*(e[:,j].T @ C[ih] @ Gamma_hat @ Gamma_hat[nvar-1])
                           + 2*critval*scale*np.kron(Gamma_hat.T, e[:,j].T) @ G[:,:,ih] @ W12[:, nvar-1]
                           + 2*critval*scale*e[:,j].T @ C[ih] @ W2[:, nvar-1]
                           )
            chat[j, ih] = (((T**.5)*scale*e[:,j].T @ C[ih] @ Gamma_hat)**2
                           -critval*(scale**2)*np.kron(Gamma_hat.T, e[:,j].T) @ G[:,:,ih] @ W1 @ (np.kron(Gamma_hat.T, e[:,j].T) @ G[:,:,ih]).T
                           -2*critval*(scale**2)*np.kron(Gamma_hat.T, e[:,j].T) @ G[:,:,ih] @ W12 @ C[ih].T @ e[:,j]
                           -critval*(scale**2)*e[:,j].T @ C[ih] @ W2 @ C[ih].T @ e[:,j]
                          )
            Deltahat[j,ih] = bhat[j,ih]**2 - (4*ahat[j,ih] * chat[j,ih])

            # Check conditions and calculate bounds accordingly
            if (ahat[j, ih]>0) and (Deltahat[j,ih]>0):            
                casedummy[j,ih] = 1
                MSWlbound[j,ih] = (-bhat[j,ih] - (Deltahat[j,ih]**.5))/(2*ahat[j,ih])
                MSWubound[j,ih] = (-bhat[j,ih] + (Deltahat[j,ih]**.5))/(2*ahat[j,ih])
            elif (ahat[j, ih]<0) and (Deltahat[j,ih]>0):
                casedummy[j,ih] = 2
                MSWlbound[j,ih] = (-bhat[j,ih] + (Deltahat[j,ih]**.5))/(2*ahat[j,ih])
                MSWubound[j,ih] = (-bhat[j,ih] - (Deltahat[j,ih]**.5))/(2*ahat[j,ih])
            elif (ahat[j, ih]>0) and (Deltahat[j,ih]<0):
                casedummy[j,ih] = 3
                MSWlbound[j,ih] = np.nan
                MSWubound[j,ih] = np.nan
            else:
                casedummy[j,ih] = 4
                MSWlbound[j,ih] = -np.inf
                MSWubound[j,ih] = np.inf

    # Set the bounds for the normalized variable
    MSWlbound[nvar-1, 0] = scale
    MSWubound[nvar-1, 0] = scale

    # Store results in a dictionary
    rv = {'l':MSWlbound, 'u':MSWubound, 'ahat':ahat, 'bhat':bhat, 'chat':chat,
          'Deltahat':Deltahat,'casedummy':casedummy}    
    return rv


def CI_dmethod_standard(Gamma_hat, WHat, G, T, C, hori=21, confidence=0.95, scale=1, nvar=1):
    '''
    Compute confidence intervals using the standard D-method.

    Input:
        Gamma_hat = estimate of Gamma according to the paper with n rows= number of endog
                    n columns=1
        WHat = Block covariance matrix = [[W1 , W12], [W12 , W2]] 
        G = Gradient matrix to be used, could be G or Gcum
        T = number of observations
        hori = 21 by default. Periods in the forecast. Same length as G.shape[1]
        C = The Ma representation of A (betas_lag) in a list form 
        confidence = 0.95 by default. Is the confidence value to be used for the critical value
        scale = 1 by default which is the normalization to 1 in the first variable
        nvar = 1 by default which is the endog variable used as the normalization in Gamma_hat

    Output:
        rv = dictionary with the confidence intervals,
           = {'lambdahatcum': lambdahatcum, 'DmethodVarcum': DmethodVarcum, 
              'Dmethodlboundcum': Dmethodlboundcum, 'Dmethoduboundcum': Dmethoduboundcum}
    '''

    # Extract dimension
    n = len(Gamma_hat)

    # Calculate critical value
    critval = norm.ppf(1-(1-confidence)/2)**2

    # Identity matrix
    e = np.eye(n)

    # Initialize arrays for storing results
    lambdahatcum = np.zeros((n, hori))
    DmethodVarcum = np.zeros((n, hori))
    Dmethodlboundcum = np.zeros((n, hori))
    Dmethoduboundcum = np.zeros((n, hori))

    # Loop through forecast periods
    for ih in range(hori):    
        # Loop through endogenous variables
        for ivar in range(n):
            # Calculate lambdahatcum
            lambdahatcum[ivar,ih] = (scale*e[:,ivar].T @ C[ih] @ Gamma_hat / Gamma_hat[nvar-1,0])[0]        
            
            # Construct matrix d for D-method
            d1 = scale * np.kron(Gamma_hat.T, e[:,ivar].T) @ G[:,:,ih]
            d2 = scale * e[:,ivar].T @ C[ih] - lambdahatcum[ivar,ih] * e[:, nvar-1].T
            d = np.concatenate([d1, d2.reshape(1,len(d2))], axis=1).T
            
            # Calculate D-method variance, lower and upper bounds
            DmethodVarcum[ivar,ih] = d.T @ WHat @ d
            Dmethodlboundcum[ivar,ih] = lambdahatcum[ivar,ih] - ((critval/T)**.5)*(DmethodVarcum[ivar,ih]**.5)/abs(Gamma_hat[nvar-1,0])
            Dmethoduboundcum[ivar,ih] = lambdahatcum[ivar,ih] + ((critval/T)**.5)*(DmethodVarcum[ivar,ih]**.5)/abs(Gamma_hat[nvar-1,0])
            del d

    # Calculate standard error for pluginirf
    std = (DmethodVarcum**.5) / ((T**.5)*np.abs(Gamma_hat[nvar-1,0]))

    # Store results in a dictionary
    rv = {'lambdahatcum':lambdahatcum, 'DmethodVarcum':DmethodVarcum, 
          'l':Dmethodlboundcum, 'u':Dmethoduboundcum, 'pluginirf':lambdahatcum,
          'pluginirfstderror':std}
    return rv
