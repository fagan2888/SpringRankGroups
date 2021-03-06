import networkx as nx
import numpy as np
from scipy import sparse as sp
from scipy.sparse import coo_matrix,spdiags,csr_matrix
import scipy.sparse.linalg

import warnings
from scipy.sparse import SparseEfficiencyWarning
warnings.simplefilter('ignore', SparseEfficiencyWarning)

def csr_SpringRank(A):
    """
    Main routine to calculate SpringRank by solving linear system
    Default parameters are initialized as in the standard SpringRank model
    
    INPUT:
        A=network adjacency matrix (can be weighted)

    OUTPUT:
        rank: N-dim array, indeces represent the nodes' indices used in ordering the matrix A
    """

    N = A.shape[0]
    k_in = np.array(A.sum(axis=0))
    k_out = np.array(A.sum(axis=1).transpose())
    
    # form the graph laplacian
    operator = csr_matrix(
        spdiags(k_out+k_in,0,N,N)-A-A.transpose()
        )
    
    # form the operator A (from Ax=b notation)
    # note that this is the operator in the paper, but augmented
    # to solve a Lagrange multiplier problem that provides the constraint
    operator.resize((N+1,N+1))
    operator[N,0] = 1
    operator[0,N] = 1

    # form the solution vector b (from Ax=b notation)
    solution_vector = np.append((k_out-k_in), np.array([0])).transpose()

    # perform the computations
    ranks = scipy.sparse.linalg.bicgstab(
        scipy.sparse.csr_matrix(operator),
        solution_vector
        )[0]

    return ranks[:-1]


def SpringRank(A, alpha=0):
    """
    Solve the SpringRank system.
    If alpha = 0, solves a Lagrange multiplier problem.
    Otherwise, performs L2 regularization to make full rank.

    Arguments:
        A: Directed network (np.ndarray)
        alpha: regularization term. Defaults to 0.

    Output:
        ranks: Solution to SpringRank
    """

    if alpha == 0:
        rank = csr_SpringRank(A)

    else:
        N = A.shape[0]
        k_in = np.sum(A, 0)
        k_out = np.sum(A, 1)

        C = A + A.T
        D1 = np.diag(k_out + k_in)
        d2 = k_out - k_in
        B = alpha + d2
        A = alpha*np.eye(N) + D1 - C
        A = scipy.sparse.csr_matrix(np.matrix(A))
        rank = scipy.sparse.linalg.bicgstab(A, B)[0] 
    
    return np.transpose(rank)


def SpringRank_groups(A, G, reg_coeff, solver):
    """
    Solve SpringRank with groups

    Arguments:
        A: The directed network (np.ndarray)
        G: Dictionary of the group assignment matrices
        reg_coeff: Dictionary of regularization coeffecients.
            Expects same keys as `G` and an additional "individual" key.
        solver: The sparse solver to be used

    Output:
        ranks: Final combined ranks (np.ndarray)
        scores: Dictionary of scores, sorted by groups.
            Has same keys as `G` and an additional "individual" key.
    """
    
    # Get array shapes
    N, M = A.shape
    assert(N == M)
    
    # Construct Laplacian
    k_in = np.sum(A, 0)
    k_out = np.sum(A, 1)
    D = np.diag(k_out + k_in)
    L = D - (A + A.T)
    
    # Make everything sparse
    L = csr_matrix(L)
    num_groups = {}
    G_sparse = {}
    for group_type, G_i in G.items():
        num_groups[group_type] = G_i.shape[1]
        G_sparse[group_type] = csr_matrix(G_i)
    
    # Construct the LHS matrix (sparse) and RHS vector (dense)
    blocks = {}
    for group_type, G_i in G.items():
        blocks[group_type] = L @ G_i
    
    K = L + reg_coeff["individual"] * sp.eye(N)
    for group_type in G:
        K = sp.hstack([K, blocks[group_type]])
    k_diff = k_out - k_in
    d_hat = k_diff
    
    for group_type, lambda_i in reg_coeff.items():
        if group_type == "individual":
            continue
        G_i_sparse = G_sparse[group_type]
        G_i = G[group_type]
        n_i = num_groups[group_type]
        current_row = G_i_sparse.T @ L
        for block_type, block in blocks.items():
            if block_type == group_type:
                current_row = sp.hstack([current_row, G_i_sparse.T @ block + lambda_i*sp.eye(n_i)])
            else:
                 current_row = sp.hstack([current_row, G_i_sparse.T @ block])
        K = sp.vstack([K, current_row])
        
        d_i = np.matmul(G_i.T, k_diff)
        d_hat = np.append(d_hat, d_i, axis=0)
    
    # Solve using sparse or iterative solvers
    if solver == 'spsolve':
        x = scipy.sparse.linalg.spsolve(K, d_hat)
    elif solver == 'bicgstab':
        output = scipy.sparse.linalg.bicgstab(K, d_hat)
        x = output[0]
    elif solver == 'lsqr':
        output = scipy.sparse.linalg.lsqr(K, d_hat)
        x = output[0]
    else:
        output = scipy.sparse.linalg.bicgstab(K, d_hat)
        x = output[0]
    
    # Make x dense
    try:
        x = x.toarray()
    except AttributeError:
        pass
    
    # Rearrange scores and compute ranks
    scores = {}
    scores["individual"] = x[:N]
    ranks = np.copy(scores["individual"])
    prev_idx = N
    for group_type, n_i in num_groups.items():
        scores[group_type] = x[prev_idx:prev_idx + n_i]
        ranks += np.matmul(G[group_type], scores[group_type])
        prev_idx += n_i
    
    return ranks, scores

       
def SpringRank_planted_network(N, beta, alpha, K, l0=0.5, return_ranks=False):
    '''

    Uses the SpringRank generative model to build a directed network.
    Can be used to generate benchmarks for hierarchical networks

    Steps:
        1. Generates the scores (default is factorized Gaussian)
        2. Extracts A_ij entries (network edges) from Poisson distribution with average related to SpringRank energy

    INPUT:
        N=# of nodes
        beta= inverse temperature, controls noise
        alpha=controls prior's variance
        K=E/N  --> average degree, controls sparsity
        l0=prior spring's rest length 
        l1=interaction spring's rest lenght

    OUTPUT:
        A: Directed network (np.ndarray)
    '''

    variance = 1 / np.sqrt(alpha * beta)
    ranks = np.random.normal(l0, variance, N)

    Z = 0
    scaled_energy = np.zeros((N, N))
    for i in range(N):
        for j in range(N):
            displacement = ranks[i] - ranks[j] - 1
            energy_ij = 0.5 * displacement * displacement
            scaled_energy[i, j] =  np.exp(-beta * energy_ij)
            Z += scaled_energy[i, j]
    c = K*N / Z

    A = np.zeros((N, N))
    for i in range(N):
        for j in range(N):
            if i == j:
                continue
            A[i, j] = np.random.poisson(c * scaled_energy[i, j])

    if return_ranks:
        return A, ranks
    else:
        return A

def SpringRank_planted_network_groups(N, num_groups, beta, alpha, K, prng, l0=0.5, l1=1,
                                      allow_self_loops=False, return_ranks=False):
    """
    Uses SpringRank generative model to build a directed, weighted network assuming group preferences.
    
    1. Randomly assign groups
    2. Generate scores assuming a normal distribution
    3. Generate network as described by the SpringRank generative model
    
    Arguments:
        N: Number of nodes
        num_groups: Dictionary of different group sizes
        beta: Inverse temperature
        alpha: Dictionary controlling individual and group scores' variance.
            Expects same keys as `num_groups` and an additional "individual" key.
        K: Average degree
        prng: Random number generator
        l0: Dictionary of individual and group scores' mean
            Expects same keys as `num_groups` and an additional "individual" key.
        l1: Spring rest length
        allow_self_loops: Allow self loops in network. Defaults to False
        return_ranks: Should we return the generated ranks. Defaults to False
    
    Output:
        A: nx.DiGraph()
        G: Dictionary of assigned group matrix
        scores: Dictionary of individual and group scores
        ranks: Generated total ranks
    """
    
    # Assign groups and generate scores

    scores = {}
    G = {}

    alpha_i = alpha["individual"]
    l0_i = l0["individual"]
    scores["individual"] = prng.normal(l0_i, 1/np.sqrt(alpha_i*beta), N)

    ranks = np.copy(scores["individual"])

    for group_type in num_groups:

        # generate groups
        n_i = num_groups[group_type]
        groups_i = np.random.randint(0, n_i, N)
        G_i = np.zeros((N, n_i))
        for j, g_j in enumerate(groups_i):
            G_i[j, g_j] = 1
        G[group_type] = G_i
        
        # generate scores
        alpha_i = alpha[group_type]
        l0_i = l0[group_type]
        scores[group_type] = prng.normal(l0_i, 1/np.sqrt(alpha_i*beta), n_i)

        # compute rank
        ranks += np.matmul(G_i, scores[group_type])
    
    # Fix sparsity using the average degree
    scaled_exp_energy = np.zeros((N, N))
    Z = 0
    for i in range(N):
        for j in range(N):
            energy_ij = 0.5 * np.power(ranks[i]-ranks[j]-l1, 2)
            scaled_exp_energy[i, j] = np.exp(-beta * energy_ij)
            Z += scaled_exp_energy[i, j]
    c = float(K * N) / Z
    
    # Build network
    A = nx.DiGraph()
    for i in range(N):
        A.add_node(i, score=ranks[i])
    
    for i in range(N):
        for j in range(N):
            if i == j and not allow_self_loops:
                continue

            lambda_ij = c * scaled_exp_energy[i, j]
            A_ij = np.random.poisson(lambda_ij)
            if A_ij > 0:
                A.add_edge(i,j,weight=A_ij)
    
    if return_ranks:
        return A, G, scores, ranks
    else:
        return A, G     