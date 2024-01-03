import numpy as np
import random
import math
import torch
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib import cm
import scipy.io
import os
import ot
import sys
import csv
import shutil
import pathlib
import argparse
from statistics import mode
sys.path.insert(0, os.path.abspath('../../'))

from wdl.bregman import barycenter
from wdl.WDL import WDL
from wdl.bregman import bregmanBary

from sklearn.decomposition import PCA
from sklearn.decomposition import NMF
from sklearn.cluster import KMeans
from sklearn.metrics import confusion_matrix
from sklearn.neighbors import kneighbors_graph
from scipy.optimize import linear_sum_assignment

#Comments: 
#Indian Pines/Salinas/Cuprite data is from the AVIRIS sensor: 
# https://purr.purdue.edu/publications/1947/1

#All of this handles the possibility of working with other files, if you are just
#working exclusively with SalinasA, just use 4. 
#Data file names
fname = 'SalinasA_correct.mat'
matname = 'salinasA_corrected'
gtfname = 'SalinasA_gt.mat'
gtname = 'salinasA_gt' 

#END OF REFERENCES

#1) Backend File IO storage/manipulation and data loading | Done

#2) WDL_instance and supporting functions, along with PCA, any algo

#3) Experiments and experimental control processes

#4) Visualization of results

#5) Miscellaneous

#Makes directory, deletes directory and subtrees if it already exists
#Variables:
#path: dir name, 
#override: if super important, set to false and will not replace if already exists
def dir_check(path, override=True):
    try: 
        os.mkdir(path)
    except: 
        if override: 
            shutil.rmtree(path)
            os.mkdir(path)
        else: 
            print('Directory already exists please delete')
            exit()        
 
#Creates initial WDL directories when running experimental loop
#Variables: 
#name: directory name
def storage(name):
    path = os.path.join(os.getcwd(), name)
    dir_check(path)
    open(name + '/params.txt', 'x')

#loads in the data for any .mat file.
#Variables:
#fname: file name, mat_name: reference within mat_name
def loadmat(fname, mat_name):
    root_path = os.path.dirname(os.path.dirname(os.getcwd()))
    path = os.path.join(root_path, fname)
    mat = scipy.io.loadmat(path) 
    return mat[mat_name]

#Makes all values in 2d array non-negative
#Variables:
#data: data, pad: increases lower bound for data 
def positivefy(data, pad=0):
    min = 0
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            if data[i,j] < min:
                min = data[i,j]
    return data + np.abs(min) + pad

#Loads in data for use
#Variable:
#mode='data' or 'gt', loads in those respective files
#NOTE: REFACTORED
def data_loader(mode='data'):
    if mode == 'data':
        data = loadmat(fname, matname)
    elif mode == 'gt':
        data = loadmat(gtfname, gtname)
    data = data.reshape(data.shape[0]*data.shape[1], -1)

    if mode != 'gt':
        data = np.delete(data, [0, 32, 94], axis=1)

    return positivefy(data) #Guarantees non-negativity, a couple channels didn't hold

#This function is meant to conduct one general full experiment with outputs and everything
#Variables: 
#k: atoms 
#index: file index that has information
#train_size: size of training data
#dir_name: directory where to store everything
#reg: entropic regularizer, mu: geometric regularizer
#max_iters: num of WDL iterations, n_restarts: num of WDL restarts
#lr: learning rate, maxsinkiters: num sinkhorn iterations in WDL
#cost_power: In cost matrix, power used for distance
#test_size: If reconstruction, number of points
#mode: Sampling method
#n_clusters: num labels want to use, 
#label_hard: allows presetting labels used
#rec_training: If true, reconstructs training data from learned info
#weights_vis: If true, visualizes CC^T block matrix
#training_data: If non_empty, means using the training data in the file name passed in

#NOTE: mu=geometric regularizer, reg=entropic regularizer
#NOTE: REFACTORED
def wdl_instance(k=2, train_size=100, dir_name='testing', reg=0.05, mu=0.1,
                 max_iters=50, n_restarts=1, lr=0.01, cost_power=1, mode='train_classes', 
                 n_clusters=2, label_hard=[], training_data=''):
    dev = torch.device('cpu') #if torch.cuda.is_available() else torch.device("cpu")
    storage(dir_name) #All results are saved to dir_name

    #Loads in training data, so if empty will generate random sample
    #If using fixed sample, will load that in, also will have to update where lst is loaded in from
    if training_data == '':
        data = data_loader('data')
        (train_data, lst, train_classes) = sample(data, train_size, mode=mode, n_labels=n_clusters, label_hard=label_hard)
        #train_data is the data, lst is the indicies in the array where each element is (if reshaped to 1d), 
        #and train_classes are the classes used. 
        train_index = torch.tensor(np.array(lst))
        torch.save(train_index, dir_name + '/train_index.pt')
    else:
        train_data = torch.load(training_data)
        lst = torch.load('common_index.pt')

    #Cost matrix, you can preload it with a file, but also Cost() generates it
    cost_mode = 'L^' + str(cost_power)
    if type(cost_power) == str:
        C = torch.load(cost_power)
    else: 
        C = Cost(cost_power)

    #Creates output file with parameters here just for tracking purposes
    with open(dir_name + '/params.txt', 'w') as file:
        file.write('cost=' + cost_mode + '\n')
        file.write('n_atoms=' + str(k) + '\n')
        file.write('mu=' + str(mu) + '\n')
        file.write('reg=' + str(reg) + '\n')
        file.write('n_restarts=' + str(n_restarts) + '\n')
        file.write('max_iter=' + str(max_iters) + '\n')
        file.write('sample size=' + str(len(lst)) + '\n')
        file.write('num clusters=' + str(n_clusters))
        
    #Does WDL 
    wdl = WDL(n_atoms=k, dir=dir_name)
    train_data = train_data.T
    (weights, V_WDL) = WDL_do(dev, wdl, train_data, C, reg, mu, max_iters, lr, n_restarts)
    torch.save(V_WDL, dir_name + '/atoms.pt')
    torch.save(weights, dir_name + '/coeff.pt')

    #Visualizes learned atoms
    for i in range(0, V_WDL.shape[1]):
        plt.plot(V_WDL[:,i])
    plt.title('Learned atoms k=' + str(k) + ' mu=' + str(mu) + ' reg=' + str(reg))
    plt.savefig(dir_name + '/atoms.pdf')
    plt.clf()     

#Makes cost matrix given csv file of costs
#Variables:
#index: file index reference, cost_power: power in cost distance
#NOTE: REFACTORED
def Cost(cost_power):
    vec = np.array([])
    size = 0
    file = str(os.path.dirname(os.path.dirname(os.getcwd()))) + '/salinas_costs.csv'
    with open(file) as csvfile:
        csvreader = csv.reader(csvfile, delimiter=',')
        for row in csvreader:
            vec = np.append(vec, float(row[2]))
            size += 1
    C = np.zeros((size, size))
    for i in range(0, C.shape[0]):
        for j in range(0, C.shape[1]):
            C[i, j] = abs(vec[i] - vec[j])**cost_power
    C = torch.tensor(C)
    C /= C.max()*0.1 #Divides like this to avoid numerical issues
    return C

#Does random sample given the HSI 
#Variables: 
#X: data
#Size: size of sample
#mode: Sampling modes: 
#   train_classes: Pulls from certain number of levels
#   true_random: Pulls 'size' points from anywhere
#   everything: Just gets all the data
#n_labels: how many labels want to sample from
#gt_index: File index used to pull gt labels 
#label_hard: If want to preset labels
#Data generated through call of sample under train classes
#NOTE: REFACTORED
def sample(X, size, mode='train_classes', n_labels=0, label_hard=[]):
    classes = set()
    lst = set()
    gt_vals = data_loader('gt')
    gt_data = loadmat(gtfname, gtname) 
    gt_data = gt_data.reshape(gt_data.shape[0]*gt_data.shape[1], -1)

    if mode == 'train_classes': #When want a certain number of training classes
        if len(label_hard) > 0:
            train_labels = label_hard
        else: #Will need to update labels for different images
            train_labels = random.sample([1, 10, 11, 12, 13, 14], n_labels)
        for i in range(1, len(train_labels) + 1):
            while len(lst) < i*size/len(train_labels): #So samples uniformly from each class
                val = random.randint(0, X.shape[0] - 1)
                k = gt_vals[val][0]
                if k == train_labels[i-1]:
                    lst.add(val)
                    classes.add(k)
    elif mode == 'true_random': #Only verifies data labeled, just gets random
        while len(lst) < size: 
            val = random.randint(0, gt_data.shape[0] - 1)
            if gt_data[val][0] != 0:
                lst.add(val)
                classes.add(gt_data[val][0])
        train_labels = sorted(list(classes))

    if not type(lst) == list: 
        lst = list(lst)

    samp = X[lst]
    samp = samp/samp.sum(axis=1)[:,None]

    if label_hard == []:
        return (samp, lst, train_labels)
    else:
        return (samp, lst, label_hard)
    
#Calls wdl.fit() function
#More variables:
#dev: device, wdl: wdl object, init_method: WDL initialization method
#Returns tuple with weights and atoms
def WDL_do(dev, wdl, data, C, reg=0.05, mu=0.1, max_iters=100, lr=0.01, n_restarts=2, init_method='kmeans++-init'):
    weights = wdl.fit(X=torch.tensor(data).to(dev), C=C,
                init_method=init_method, loss_method="bregman",
                bary_method="bregman", reg=reg, mu=mu, max_iters=max_iters,
                max_sinkhorn_iters=50, jointOptimizer=torch.optim.Adam,
                jointOptimKWargs={"lr": lr}, verbose=True, n_restarts=n_restarts,
                log_iters=5, log=False)
    weights = weights.to("cpu")
    V_WDL = wdl.D.detach().to("cpu")
    return (weights, V_WDL)

#Normalized spectral clustering
#Inputs: X: data, n_components: number of components
def spectral_cluster(X, n_components):
    Dsqrt = np.zeros((X.shape[0], X.shape[0]))
    for i in range(0, X.shape[0]):
        Dsqrt[i, i] = 1 / math.sqrt(np.sum(X[i, :]) - X[i, i])
    # inefficient symetric laplacian construction
    Lsym = np.eye(X.shape[0]) - Dsqrt @ X @ Dsqrt

    eval_sym, evec_sym = np.linalg.eigh(Lsym)
    
    V = np.zeros((X.shape[0], n_components)) #Smallest k eigenvectors
    for i in range(n_components):
        V[:,i] = evec_sym[:,i] #Now not skipping first
    #normalize row norms to 1
    V /= np.linalg.norm(V, axis=1).reshape(-1,1)

    km = KMeans(init='random', n_init='auto', n_clusters=n_components)
    km.fit(V)
    return km.labels_  

#Does K nearest neighbors on the data set
#Variables:
#W: matrix, neighbors: number of NN
#constraint: loose/tight/other, etc.
def kneighbor_weights(W, neighbors, constraint):
    W = W.T
    #Default distance is L2
    A = kneighbors_graph(W, neighbors, mode='connectivity', include_self=True)
    A = A.toarray()
    
    #None: None
    #tight: 1 if both are NN of each other, 0 otherwise
    #loose: 1 if at least one of them is NN, 0 otherwise
    if constraint == 'none': 
        return A 
    elif constraint == 'and': #exclusive/both/tight
        return np.multiply(A, A.T)
    elif constraint == 'or': #either/or
        return np.ceil((A + A.T)/2)

#Given directory name used in samples (big_sample...), gets atoms, mu, reg vals
#Variable:
#path_temp: the path used
def path_convert(path_temp): 
    path_temp_k = path_temp[path_temp.find('_k=') + 1:(path_temp.find('_mu='))]
    path_temp_k = float(path_temp_k.replace('k=', ''))
    path_temp = path_temp[path_temp.find('_mu='):]
    second = path_temp.find('_') + 1
    path_temp_mu = path_temp[second: path_temp.find('_reg=')]
    path_temp_mu = float(path_temp_mu.replace('mu=', ''))
    path_temp = path_temp[second:]
    path_temp = path_temp[path_temp.find('_reg=') + 1:]
    path_temp_reg = path_temp.replace('reg=', '')
    path_temp_reg = float(path_temp_reg)
    
    return (path_temp_k, path_temp_mu, path_temp_reg)


#Clustering loop that goes through WDL results, does spectral clustering on NN
#and also some other minor things. Idea is we have big parent directory and are 
#looking through it's subdirectories. 
#Variables: 
#core_dir: sub directory where we are running clustering on
#NN_mode: Type of NN mentioned in kneighbors graph
#gen_mode: The only type is HPC or other. HPC means this is really only being
#run on one directory. Other name means run on everything
#par_dir: Directory we are looking through to run everything
#savename: string for what you want to save the resulting NN matrix as. 
#train_mode: 'global'= using same data for everything, so loads that in. 
#recon: Will get reconstructions of training data if true. 
def clustering_loop(core_dir='big_sample_k=', NN_mode='or', par_dir='', 
                    savename='', train_mode='global', recon=False):
    
    #Sets up the color map, use remap to reassign labels as matplotlib coloring
    #can be a little weird at times. 
    remap = {0: 0, 1: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 6}
    cmap = cm.get_cmap('viridis', 7)
    new_cmap = mcolors.ListedColormap(cmap.colors)
    new_cmap.colors[0] = (1, 1, 1, 1)

    test_neighbors = [20, 25, 30, 35, 40, 45, 50] ##NN we use

    params = np.zeros((len(test_neighbors)*1500, 5))
    
    #This remaps the GT, and makes a mask matrix. Mask is 1 if data is labeled, 
    #0 otherwise. 
    gt_data = data_loader('gt')
    mask = np.zeros(83*86)
    for i in range(gt_data.shape[0]):
        gt_data[i] = remap.get(gt_data[i][0])
        if gt_data[i] != 0:
            mask[i] = 1
    mask = np.reshape(mask, (83, 86))

    #If we do reconstructions, we need the cost matrix, loading it in here
    if recon: 
        C = Cost(1)

    #The main loop. Iterates through neighbors, then goes through each directory
    #All of this is embedded in try/except to check things work
    c = 0
    for neighbors in test_neighbors: 
        for path in pathlib.Path(os.getcwd() + par_dir).iterdir():
            path_temp = str(path)
            #Essentially checks if valid directory and that it works
            try: 
                #Gets the k, mu, and reg values. 
                #Note path_convert() is reliant on the directory name being
                #consistent with big_sample_k=*_mu=*_reg=*
                (temp_k, temp_mu, temp_reg) = path_convert(path_temp)
            except:
                continue
            #If this is the right directory
            if core_dir in path_temp:
                #We use a try/except as every once in a while, normalized SC can
                #fail. 
                try:
                    weights = torch.load(path_temp + '/coeff.pt')
                    #Will do reconstructions, note that only do on 20 neighbors
                    #as it's the same regardless of that.
                    if recon and neighbors == 20: 
                        atoms = torch.load(path_temp + '/atoms.pt')
                        barySolver = barycenter(C=C, method="bregman", reg=temp_reg, maxiter=100)
                        rec = np.zeros((atoms.shape[0], weights.shape[1]))
                        for i in range(weights.shape[1]):
                            rec[:,i] = barySolver(atoms, weights[:,i]).numpy().reshape(201,)
                        plt.plot(rec)
                        plt.title('WDL Reconstruction k=' + str(temp_k) + ' reg=' + str(temp_reg) + ' geom=' + str(temp_mu))
                        plt.savefig(path_temp + '/WDL_reconstruction.pdf')
                        np.save(path_temp + '/reconstructions', rec)
                        plt.close()
                    weights = weights.numpy()
                    #Normalizes the weights, and then constructs the block matrix
                    for i in range(0, weights.shape[1]):
                        weights[:,i] = weights[:,i]/np.linalg.norm(weights[:,i])
                    weights = weights.T @ weights
                    #Gets kneighbors matrix
                    W = kneighbor_weights(weights, neighbors, constraint=NN_mode)

                    #Does spectral clustering on that matrix
                    labels = spectral_cluster(W, 6)
                except:
                    continue
                label_set = sorted(list(set(labels)))
                #If using global data, will have to update what file name loading
                #in for index. 
                if train_mode != 'global':
                    index = torch.load(path_temp + '/train_index.pt').numpy()
                else:
                    index = torch.load('common_index.pt')
                
                #Loads in gt, gt_grapher does it for visual purposes, gt_temp 
                #gets the gt of each actual label.
                gt_grapher = np.zeros(83*86)
                gt_temp = np.zeros(len(index))
                for i in range(index.shape[0]):
                    k = int(gt_data[index[i]])
                    gt_temp[i] = k
                    gt_grapher[index[i]] = k

                #We need to remap the resulting SC labels to the correct ones
                gt_labs = np.array(list(set(gt_temp)))
                for i in range(len(labels)):
                    labels[i] = gt_labs[label_set.index(labels[i])]

                #Linear assignment to match clusters correctly
                confusion = confusion_matrix(gt_temp, labels)
                cost_final = -confusion + np.max(confusion)
                (res1, res2) = linear_sum_assignment(cost_final)

                #Remaps labels again for visualization purpose
                temp2 = list(gt_labs[res2])
                for i in range(0, len(labels)):
                    labels[i] = temp2.index(labels[i]) + 1

                #Gets accuracy score, goes through and checks if the label matches
                #the gt. 
                acc = 0
                train_plot = np.zeros(83*86)
                for i in range(len(labels)):
                    t = index[i]
                    j = labels[i]
                    train_plot[t] = j
                    if gt_data[t] == j:
                        acc += 1

                #Accuracy percentage, along with printing out results
                acc = acc/len(labels) 
                print('atoms=' + str(temp_k), 'geom=' + str(temp_mu), 
                      'entropy=' + str(temp_reg), '| acc=' + str(acc))
                
                #Makes the ground truth plot
                train_plot = np.reshape(train_plot, (83, 86))  
                if  train_mode != 'global' and neighbors == 20: 
                    gt_grapher = np.reshape(gt_grapher, (83, 86))
                    plt.imshow(gt_grapher, cmap=cmap)
                    plt.title('Ground truth')
                    plt.savefig(path_temp + '/gt.pdf')
                    plt.clf()
                
                #Now this runs spatial_NN on the image. It can be a bit slow, 
                #so it will only run if the accuracy is above 60%. 
                if acc >= 0.6:
                    spatial_NN(train_plot, 10, new_cmap, path_temp, temp_k, temp_reg, temp_mu, neighbors, mask)
                
                #Makes the plot of the result
                plt.imshow(train_plot, cmap=cmap)
                plt.tick_params(left = False, right = False, labelleft = False, 
                labelbottom = False, bottom = False) 
                plt.title('Learned labels ' + 'atoms=' + str(temp_k) + ' mu=' + str(temp_mu) 
                            + ' reg=' + str(temp_reg) +  ' accuracy=' + str(round(acc, 2)))
                plt.savefig(path_temp + '/learned_loose_clean_Acc=' + str(round(acc, 2)) + '_NN=' + str(neighbors) + '.pdf'
                            , bbox_inches='tight')
                plt.clf()

                #Saves the parameters and accuracy 
                params[c,:] = [temp_mu, temp_k, temp_reg, neighbors, acc]
                c += 1
    #Removes all rows that are all zeros, and then saves the matrix.
    params = params[~np.all(params == 0, axis=1)] 
    np.save(NN_mode.title() + '_' + savename, params)


#Buckets: support
def uniform(buckets, a, b):
    X = np.zeros(buckets)
    for i in range(X.shape[0]):
        if i <= a: 
            X[i] = 1/(8*(b-a))
        if i >= b:
            X[i] = 1/(8*(b-a))
        if i > a and i < b:
            X[i] = 1/(b-a)
    X /= np.sum(X)
    return X

def laplace(buckets, mu, b):
    Y = np.arange(0, buckets, dtype='float64')
    for i in range(Y.shape[0]):
        k = -1*abs(Y[i] - mu)
        Y[i] = math.exp(k/b)/(2*b)
    Y /= np.sum(Y, axis=0)
    return Y

def synthetic_experiments(samp=51, reg=0.001, mu=0, lm=True):
    #Directory where want outputs
    dir_name = 'synth_test_rho=0.1_entropy=' + str(reg)
    size = 200 #Number of buckets 

    #Atom creation
    test = np.zeros((2, size))
    # test[0,:] = uniform(200, 20, 80)
    # test[1,:] = laplace(200, 140, 4)
    test[0,:] = ot.datasets.make_1D_gauss(size, m=50, s=5)
    test[1,:] = ot.datasets.make_1D_gauss(size, m=130, s=10)

    pca_model = PCA(n_components=2)
    nmf_model = NMF(n_components=2)

    #Visualizes synthetic atoms
    plt.gca().set_prop_cycle(plt.cycler('color', plt.cm.viridis(np.linspace(1, 0, 2))))
    plt.plot(test.T)
    plt.ylim(-0.025, 0.1)
    plt.savefig(dir_name + '/synth_atoms.pdf', bbox_inches='tight')
    plt.close()

    #Creates the weights for generating the barycenters
    weights = np.zeros((samp, 2))
    for i in range(0, samp):
        weights[i,:] = np.array([0.02*i, 1 - 0.02*i])

    #Makes cost matrix for barycenters
    M = ot.utils.dist0(size)
    M /= M.max()
    synth_data = np.zeros((samp, size))

    #Need to save M/M_old for when running WDL 
    M_old = torch.tensor(M) 
    M = torch.tensor(np.exp(-M/reg)) #Kernel
    test = test.T
    
    #Gets synthetic data
    if lm: 
        test_dup = np.copy(test)
        test_dup = test_dup.T
        synth_lm = np.zeros((samp, size))
    for i in range(0, samp):
        if lm:
            synth_lm[i,:] = weights[i,0]*test_dup[0,:] + weights[i,1]*test_dup[1,:]
        res = bregmanBary(torch.tensor(test), torch.tensor(weights[i,:]).view(-1, 1), M, reg=reg, maxiter=1000).numpy()
        synth_data[i,:] = res.reshape(res.shape[0],)

    if lm: #linear mixture visualization
        plt.gca().set_prop_cycle(plt.cycler('color', plt.cm.viridis(np.linspace(0, 1, samp))))
        synth_lm = torch.tensor(synth_lm.T) #Plots synthetic data
        plt.plot(synth_lm)
        plt.ylim(-0.025, 0.1)
        plt.savefig(dir_name + '/linear_mixture.pdf', bbox_inches='tight')
        plt.close()

    #Synthetic data visualization
    plt.gca().set_prop_cycle(plt.cycler('color', plt.cm.viridis(np.linspace(0, 1, samp))))
    #synth_data = torch.tensor(synth_data.T) #Plots synthetic data
    plt.plot(synth_data.T)
    plt.ylim(-0.025, 0.1)
    plt.savefig(dir_name + '/synth_data.pdf', bbox_inches='tight')
    plt.close()
    np.save(dir_name + '/synth_data', synth_data)

    #PCA model
    train = pca_model.fit_transform(synth_data) #PCA
    eigen = pca_model.components_
    inv = pca_model.inverse_transform(train) #PCA reconstruction

    #NMF model
    W = nmf_model.fit_transform(synth_data) #NMF 
    H = nmf_model.components_
    X = W @ H #NMF reconstruction

    #For visualizing, the cycler() makes the colors line up
    #PCA visualization
    plt.gca().set_prop_cycle(plt.cycler('color', plt.cm.viridis(np.linspace(1, 0, 2))))
    plt.plot(eigen.T) #PCA components
    plt.ylim(-0.13, 0.2)
    plt.savefig(dir_name + '/PCA_evector.pdf', bbox_inches='tight')
    plt.clf()

    #NMF visualization
    plt.gca().set_prop_cycle(plt.cycler('color', plt.cm.viridis(np.linspace(1, 0, 2))))
    plt.plot(H.T) #NMF components
    plt.ylim(-0.025, 0.2)
    plt.savefig(dir_name + '/NMF_components.pdf', bbox_inches='tight')
    plt.clf()

    #PCA visualization
    plt.gca().set_prop_cycle(plt.cycler('color', plt.cm.viridis(np.linspace(0, 1, samp))))
    plt.plot(inv.T) #PCA reconstructions
    plt.ylim(-0.025, 0.1)
    plt.savefig(dir_name + '/PCA_reconstruct.pdf', bbox_inches='tight')
    plt.clf()

    #NMF visualization
    plt.gca().set_prop_cycle(plt.cycler('color', plt.cm.viridis(np.linspace(0, 1, samp))))
    plt.plot(X.T) #NMF reconstructions
    plt.ylim(-0.025, 0.1)
    plt.savefig(dir_name + '/NMF_reconstruction.pdf', bbox_inches='tight')
    plt.close()

    #Runs WDL on the data, as it's small, you can set n_restarts/max_iters pretty high
    #and it should run reasonably fast.
    wdl = WDL(n_atoms=2, dir=dir_name)
    synth_data = synth_data.T
    barySolver = barycenter(C=M_old, method="bregman", reg=reg, maxiter=1000)
    (weights, V_WDL) = WDL_do(dev, wdl, synth_data, M_old, reg, mu, n_restarts=5, max_iters=2500)
    np.save(dir_name + '/atoms', V_WDL)
    np.save(dir_name + '/weights', weights)

    #Learned atoms visualization
    plt.gca().set_prop_cycle(plt.cycler('color', plt.cm.viridis(np.linspace(0, 1, 2))))
    plt.plot(V_WDL) #Plots learned atoms
    plt.ylim(-0.025, 0.1)
    plt.savefig(dir_name + '/learned_atoms.pdf', bbox_inches='tight')
    plt.close()

    #Reconstructions visualization
    plt.gca().set_prop_cycle(plt.cycler('color', plt.cm.viridis(np.linspace(0, 1, samp))))
    rec = np.zeros((V_WDL.shape[0], samp))
    for i in range(weights.shape[1]): #Gets reconstructions
        rec[:,i] = barySolver(V_WDL, weights[:,i]).numpy().reshape(size,)
    plt.plot(rec) #Plots them
    plt.ylim(-0.025, 0.1)
    plt.savefig(dir_name + '/reconstructions.pdf', bbox_inches='tight')
    plt.close()


#So to actually run these experiments, I used the Tufts Research Cluster (HPC)
#So tasks were submitted using shell scripts and order was done using slurm. 
#To compartmentalize and make sure things run efficiently, rather than running
#one experiment, I essentially set the values of mu/num atoms, then go through
#the chosen values of reg. 
def control_loop():
    parser = argparse.ArgumentParser() #Reads in command line arguments
    parser.add_argument("--n_atoms", type=int)
    parser.add_argument("--geom", type=float)
    parser.add_argument("--recip", type=str)
    regs = [0.02, 0.05, 0.08, 0.1]
    args = parser.parse_args()
    k = args.n_atoms
    mu = args.geom
    recip = args.recip
    
    #Shell scripts can't represent non-integers, so as the <1 values of mu are
    #0.1, 0.01, and 0.001, we get around it by saying 10, 100, 1000 and taking reciprocal.
    if recip.lower() == 'true':
        mu = 1/mu #sh scripts don't allow non-integers, so this gets around that
   
    #In addition to doing WDL, this will also run the clustering loop on the results.
    for reg in regs: 
        name = 'big_fixed_sample_k=' + str(k) + '_mu=' + str(mu) + '_reg=' + str(reg)
        wdl_instance(k=k, train_size=1002, dir_name=name, reg=reg, mu=mu,
                        max_iters=250, n_restarts=1, cost_power=1, 
                        mode = 'true_random', n_clusters=6, 
                        label_hard=[1, 10, 11, 12, 13, 14], rec_training=False,
                        weights_vis=False, training_data='common_data.pt')

#So this performs Spatial K-NN
#Now this is near exclusively run inside clustering_loop() so some of these params
#might just be 0/not used.
#Inputs: 
#X: data nn: number of nn used in Spatial NN
#cmap: color map
#dir_name: directory where it's saved
#temp_k: #atoms reg: entropy mu: geom regularizer
#init_nn: #NN used in learned model 
#mask: Mask so only getting result on labeled point
def spatial_NN(X, nn, cmap, dir_name, temp_k, reg, mu, init_nn, mask): 
    #SIt's important that we only use initially labeled pixels when updating the array
    data = np.copy(X)
    c = 0

    #Outer double loop goes through every pixel, we only do stuff if labeled
    for i in range(X.shape[0]):
        for j in range(X.shape[1]):
            if X[i, j] == 0: 
                #3 tracking vars
                count = 0 # of nn currently added
                curr_dist = 1 #Distance from (i, j) looking at
                tracks = np.zeros(nn) #Stores results
                
                #Pretty much the k-NN will be in some subgrid of the image, so 
                #we check that grid, everything d =1, 2... so we get closest. 
                #Technically ties aren't broken at random, but don't think that matters.
                while tracks[nn-1] == 0 and curr_dist < 40: 
                    for k in range(max(i - curr_dist - 1, 0), min(i + curr_dist + 1, data.shape[0])):
                        for l in range(max(j - curr_dist - 1, 0), min(j + curr_dist + 1, data.shape[1])):
                            #So we want the vote to have a label, and check in distance range
                            if X[k,l] != 0 and euc_dist((k, l), (i, j), norm=1) == curr_dist: 
                                tracks[count] = X[k,l]
                                count += 1
                                if count == nn: #Don't continue if have the 10
                                    break
                        if count == nn: #Will double break and exit the loop
                            break
                    curr_dist += 1
                tracks = tracks[tracks != 0] #Just in case we don't get the amount
                data[i, j] = mode(tracks) #Most frequent element
            c += 1
    colors = cmap(np.linspace(0, 1, cmap.N))
    new_cmap = mcolors.ListedColormap(colors)

    data = np.multiply(data, mask) #Masks and visualizes the data
    plt.imshow(data, cmap=new_cmap)
    plt.tick_params(left = False, right = False, labelleft = False, labelbottom = False, bottom = False) 
    plt.title('Spatial 10-NN masked k=' + str(temp_k) + ' reg=' + str(reg) + ' mu=' + str(mu))
    plt.savefig(dir_name + '/Spatial_10-NN_masked_init=' + str(init_nn) + '.pdf', bbox_inches='tight')
    plt.clf()

def euc_dist(X, Y, norm):
    return (np.abs(X[0] - Y[0])**norm + np.abs(X[1] - Y[1])**norm)

#Given the data sets, will calculate PCA/NMF on them
#data is the data used
def get_pca_nmf(data):
    #All number of atoms/components used 
    for k in [2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36]:
        dir_name = 'PCA_NMF_comparisons/components=' + str(k)
        pca = PCA(n_components=k)
        nmf = NMF(n_components=k, max_iter=1000)

        train = pca.fit_transform(data) #PCA
        eigen = pca.components_
        inv = pca.inverse_transform(train) #PCA reconstruction

        W = nmf.fit_transform(data) #NMF 
        H = nmf.components_
        X = W @ H #NMF reconstruction

        plt.plot(eigen.T)
        plt.savefig(dir_name + '/PCA_eigenvectors.pdf', bbox_inches='tight')
        plt.clf()
        plt.plot(inv.T)
        plt.savefig(dir_name + '/PCA_reconstructions.pdf', bbox_inches='tight')
        plt.clf()

        plt.plot(H.T)
        plt.savefig(dir_name + '/NMF_components.pdf', bbox_inches='tight')
        plt.clf()
        plt.plot(X.T)
        plt.savefig(dir_name + '/NMF_reconstructions.pdf', bbox_inches='tight')
        plt.clf()

def plot_training():
    X = torch.load('common_data.pt')
    plt.plot(X.T)
    plt.savefig('salinasA_common_data.pdf')

#Main function I put all code here
if __name__ == "__main__":
    print('Imports complete') #Just some default stuff, change dev if using gpus
    torch.set_default_dtype(torch.float64) 
    dev = torch.device('cpu')
    np.set_printoptions(suppress=True)
    
    synthetic_experiments(mu=0.1, lm=False)